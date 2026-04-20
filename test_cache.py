"""
Tests for the semantic cache.
Uses mocked Redis and mocked OpenAI embedding calls.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from src.cache.semantic_cache import SemanticCache


def make_embedding(value: float = 0.5, dims: int = 256) -> np.ndarray:
    """Return a unit vector embedding for testing."""
    vec = np.full(dims, value, dtype=np.float32)
    return vec / np.linalg.norm(vec)


@pytest.fixture
def mock_redis():
    r = AsyncMock()
    r.execute_command = AsyncMock(return_value=None)
    r.hset = AsyncMock()
    r.expire = AsyncMock()
    r.get = AsyncMock(return_value=None)
    return r


@pytest.fixture
def cache(mock_redis):
    c = SemanticCache(mock_redis)
    c.threshold = 0.95
    return c


class TestSemanticCacheInitialize:
    @pytest.mark.asyncio
    async def test_creates_index_when_missing(self, cache, mock_redis):
        mock_redis.execute_command.side_effect = [
            Exception("Index not found"),   # FT.INFO raises
            None,                            # FT.CREATE succeeds
        ]
        await cache.initialize()
        calls = [str(c) for c in mock_redis.execute_command.call_args_list]
        assert any("FT.CREATE" in c for c in calls)

    @pytest.mark.asyncio
    async def test_skips_creation_when_index_exists(self, cache, mock_redis):
        mock_redis.execute_command.return_value = ["index_name", "prompt_cache"]
        await cache.initialize()
        calls = [str(c) for c in mock_redis.execute_command.call_args_list]
        assert not any("FT.CREATE" in c for c in calls)


class TestSemanticCacheLookup:
    @pytest.mark.asyncio
    async def test_returns_none_on_miss(self, cache, mock_redis):
        mock_redis.execute_command.return_value = [0]  # 0 results from FT.SEARCH

        with patch.object(cache, "_embed", AsyncMock(return_value=make_embedding(0.5))):
            result = await cache.lookup("What is Python?")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_result_on_hit(self, cache, mock_redis):
        # Simulate FT.SEARCH returning 1 result with score 0.02 (similarity = 0.98)
        mock_redis.execute_command.return_value = [
            1,
            "cache:prompt:job_xyz",
            ["response", "Python is a programming language.", "job_id", "job_xyz", "score", "0.02"],
        ]

        with patch.object(cache, "_embed", AsyncMock(return_value=make_embedding(0.5))):
            result = await cache.lookup("What is Python?")

        assert result is not None
        assert result["response"] == "Python is a programming language."
        assert result["similarity"] >= 0.95

    @pytest.mark.asyncio
    async def test_returns_none_when_similarity_below_threshold(self, cache, mock_redis):
        # score = 0.10 → similarity = 0.90, below threshold of 0.95
        mock_redis.execute_command.return_value = [
            1,
            "cache:prompt:job_xyz",
            ["response", "Something unrelated", "job_id", "job_xyz", "score", "0.10"],
        ]

        with patch.object(cache, "_embed", AsyncMock(return_value=make_embedding(0.5))):
            result = await cache.lookup("Tell me about dinosaurs")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_redis_error(self, cache, mock_redis):
        mock_redis.execute_command.side_effect = Exception("Redis connection error")

        with patch.object(cache, "_embed", AsyncMock(return_value=make_embedding(0.5))):
            result = await cache.lookup("Any prompt")

        assert result is None  # Graceful degradation


class TestSemanticCacheStore:
    @pytest.mark.asyncio
    async def test_stores_entry(self, cache, mock_redis):
        with patch.object(cache, "_embed", AsyncMock(return_value=make_embedding(0.5))):
            await cache.store(
                job_id="job_123",
                prompt="What is Python?",
                model="gpt-4o-mini",
                response="Python is a programming language.",
            )

        mock_redis.hset.assert_called_once()
        call_kwargs = mock_redis.hset.call_args
        assert "job_123" in str(call_kwargs)

    @pytest.mark.asyncio
    async def test_store_failure_does_not_raise(self, cache, mock_redis):
        mock_redis.hset.side_effect = Exception("Storage error")

        with patch.object(cache, "_embed", AsyncMock(return_value=make_embedding(0.5))):
            # Should not raise; logs warning instead
            await cache.store("job_123", "prompt", "gpt-4o-mini", "response")
