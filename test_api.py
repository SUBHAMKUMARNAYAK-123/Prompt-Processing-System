"""
Tests for the prompt queue REST API.
Uses FastAPI TestClient with mocked Redis and cache dependencies.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import create_app


@pytest.fixture
def mock_redis():
    r = AsyncMock()
    r.ping = AsyncMock(return_value=True)
    r.hset = AsyncMock()
    r.hgetall = AsyncMock(return_value={})
    r.zadd = AsyncMock()
    r.zcard = AsyncMock(return_value=0)
    r.pipeline = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=AsyncMock(
        zadd=AsyncMock(),
        hset=AsyncMock(),
        expire=AsyncMock(),
        set=AsyncMock(),
        execute=AsyncMock(return_value=[1, 1, 1]),
    )), __aexit__=AsyncMock(return_value=False)))
    return r


@pytest.fixture
def mock_cache():
    cache = AsyncMock()
    cache.lookup = AsyncMock(return_value=None)      # No cache hit by default
    cache.store = AsyncMock()
    cache.size = AsyncMock(return_value=0)
    cache.initialize = AsyncMock()
    return cache


@pytest.fixture
def mock_producer():
    producer = AsyncMock()
    producer.enqueue = AsyncMock(return_value="job_abc123")
    producer.get_job = AsyncMock(return_value=None)
    producer.queue_depth = AsyncMock(return_value=0)
    producer.active_worker_count = AsyncMock(return_value=2)
    producer.cancel_job = AsyncMock(return_value=True)
    producer.get_by_idempotency_key = AsyncMock(return_value=None)
    producer.initialize = AsyncMock()
    producer.close = AsyncMock()
    return producer


@pytest.fixture
def client(mock_redis, mock_cache, mock_producer):
    app = create_app()

    # Override lifespan dependencies
    app.state.redis = mock_redis
    app.state.cache = mock_cache
    app.state.producer = mock_producer

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


# ── Submit Prompt ─────────────────────────────────────────────────────────────

class TestSubmitPrompt:
    def test_submit_returns_202(self, client, mock_producer):
        response = client.post("/api/v1/prompts", json={
            "prompt": "What is the capital of France?",
        })
        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "queued"
        assert data["cache_hit"] is False
        assert "job_id" in data

    def test_submit_cache_hit_returns_result(self, client, mock_cache):
        mock_cache.lookup.return_value = {
            "job_id": "job_old123",
            "response": "Paris is the capital of France.",
            "similarity": 0.98,
        }
        response = client.post("/api/v1/prompts", json={
            "prompt": "What is the capital of France?",
        })
        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "cache_hit"
        assert data["cache_hit"] is True
        assert data["result"] == "Paris is the capital of France."

    def test_submit_empty_prompt_rejected(self, client):
        response = client.post("/api/v1/prompts", json={"prompt": ""})
        assert response.status_code == 422

    def test_submit_invalid_model_rejected(self, client):
        response = client.post("/api/v1/prompts", json={
            "prompt": "Hello",
            "model": "gpt-99-turbo",
        })
        assert response.status_code == 422

    def test_submit_priority_range(self, client):
        # Priority 11 out of range
        response = client.post("/api/v1/prompts", json={
            "prompt": "Hello",
            "priority": 11,
        })
        assert response.status_code == 422

    def test_idempotency_key_deduplication(self, client, mock_producer):
        mock_producer.get_by_idempotency_key.return_value = {
            "job_id": "job_existing",
            "status": "completed",
            "prompt": "Hello",
            "model": "gpt-4o-mini",
            "result": "Hi there!",
            "error": None,
            "cache_hit": False,
            "tokens_used": 10,
            "processing_time_ms": 500.0,
            "created_at": "2026-04-20T00:00:00Z",
            "started_at": None,
            "completed_at": None,
            "attempts": 1,
        }
        response = client.post("/api/v1/prompts", json={
            "prompt": "Hello",
            "idempotency_key": "my-unique-key",
        })
        assert response.status_code == 202
        # Should not call enqueue again
        mock_producer.enqueue.assert_not_called()


# ── Get Job ───────────────────────────────────────────────────────────────────

class TestGetJob:
    def test_get_existing_job(self, client, mock_producer):
        mock_producer.get_job.return_value = {
            "job_id": "job_abc123",
            "status": "completed",
            "prompt": "What is 2+2?",
            "model": "gpt-4o-mini",
            "result": "4",
            "error": None,
            "cache_hit": False,
            "tokens_used": 5,
            "processing_time_ms": 320.0,
            "created_at": "2026-04-20T00:00:00Z",
            "started_at": "2026-04-20T00:00:01Z",
            "completed_at": "2026-04-20T00:00:02Z",
            "attempts": 1,
        }
        response = client.get("/api/v1/prompts/job_abc123")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert data["result"] == "4"

    def test_get_nonexistent_job_returns_404(self, client, mock_producer):
        mock_producer.get_job.return_value = None
        response = client.get("/api/v1/prompts/job_doesnotexist")
        assert response.status_code == 404


# ── Cancel Job ────────────────────────────────────────────────────────────────

class TestCancelJob:
    def test_cancel_queued_job(self, client, mock_producer):
        mock_producer.cancel_job.return_value = True
        response = client.delete("/api/v1/prompts/job_abc123")
        assert response.status_code == 204

    def test_cancel_active_job_returns_409(self, client, mock_producer):
        mock_producer.cancel_job.return_value = False
        response = client.delete("/api/v1/prompts/job_active")
        assert response.status_code == 409


# ── Health ────────────────────────────────────────────────────────────────────

class TestHealth:
    def test_health_ok(self, client):
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "queue_depth" in data
        assert "workers_active" in data
