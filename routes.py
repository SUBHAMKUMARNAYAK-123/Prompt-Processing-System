"""
Route handlers for the prompt queue API.
"""
from __future__ import annotations

import asyncio
import time
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

from src.api.schemas import (
    HealthResponse,
    JobStatus,
    JobStatusResponse,
    PromptRequest,
    PromptSubmitResponse,
)
from src.cache.semantic_cache import SemanticCache
from src.queue.producer import JobProducer
from src.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()


def get_cache(request: Request) -> SemanticCache:
    return request.app.state.cache


def get_producer(request: Request) -> JobProducer:
    return request.app.state.producer


# ── Submit Prompt ─────────────────────────────────────────────────────────────

@router.post(
    "/prompts",
    response_model=PromptSubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit a prompt for processing",
)
async def submit_prompt(
    body: PromptRequest,
    cache: SemanticCache = Depends(get_cache),
    producer: JobProducer = Depends(get_producer),
):
    """
    Submit a prompt. If a semantically equivalent prompt exists in cache,
    returns the cached result immediately (status=cache_hit).
    Otherwise, enqueues for async processing and returns a job_id to poll.
    """
    import datetime

    now = datetime.datetime.utcnow().isoformat() + "Z"

    # 1. Idempotency check
    if body.idempotency_key:
        existing = await producer.get_by_idempotency_key(body.idempotency_key)
        if existing:
            logger.info("Idempotency key hit", key=body.idempotency_key)
            return PromptSubmitResponse(**existing, created_at=now)

    # 2. Semantic cache check
    cache_result = await cache.lookup(body.prompt, model=body.model.value)
    if cache_result:
        logger.info("Cache hit", similarity=cache_result["similarity"])
        return PromptSubmitResponse(
            job_id=f"cache_{cache_result['job_id']}",
            status=JobStatus.CACHE_HIT,
            cache_hit=True,
            result=cache_result["response"],
            created_at=now,
        )

    # 3. Enqueue job
    job_id = await producer.enqueue(
        prompt=body.prompt,
        model=body.model.value,
        priority=body.priority,
        max_tokens=body.max_tokens,
        temperature=body.temperature,
        system_prompt=body.system_prompt,
        webhook_url=body.webhook_url,
        idempotency_key=body.idempotency_key,
    )

    queue_depth = await producer.queue_depth()
    estimated_wait = queue_depth / 5.0  # ~5 jobs/sec throughput estimate

    logger.info("Job enqueued", job_id=job_id, priority=body.priority, queue_depth=queue_depth)

    return PromptSubmitResponse(
        job_id=job_id,
        status=JobStatus.QUEUED,
        cache_hit=False,
        estimated_wait_seconds=round(estimated_wait, 1),
        created_at=now,
    )


# ── Get Job Status ─────────────────────────────────────────────────────────────

@router.get(
    "/prompts/{job_id}",
    response_model=JobStatusResponse,
    summary="Get job status and result",
)
async def get_job(
    job_id: str,
    producer: JobProducer = Depends(get_producer),
):
    job = await producer.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return JobStatusResponse(**job)


# ── Stream Job Progress ────────────────────────────────────────────────────────

@router.get(
    "/prompts/{job_id}/stream",
    summary="SSE stream of job progress",
)
async def stream_job(
    job_id: str,
    producer: JobProducer = Depends(get_producer),
):
    async def event_generator() -> AsyncGenerator[str, None]:
        while True:
            job = await producer.get_job(job_id)
            if not job:
                yield f"event: error\ndata: Job not found\n\n"
                return

            yield f"event: status\ndata: {job['status']}\n\n"

            if job["status"] in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
                import json
                yield f"event: complete\ndata: {json.dumps(job)}\n\n"
                return

            await asyncio.sleep(0.5)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ── Cancel Job ────────────────────────────────────────────────────────────────

@router.delete(
    "/prompts/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cancel a queued job",
)
async def cancel_job(
    job_id: str,
    producer: JobProducer = Depends(get_producer),
):
    cancelled = await producer.cancel_job(job_id)
    if not cancelled:
        raise HTTPException(
            status_code=409,
            detail="Job cannot be cancelled (already active, completed, or not found)",
        )


# ── Health ────────────────────────────────────────────────────────────────────

_start_time = time.time()


@router.get("/health", response_model=HealthResponse, summary="Health check")
async def health(request: Request, producer: JobProducer = Depends(get_producer)):
    redis = request.app.state.redis
    try:
        await redis.ping()
        redis_status = "ok"
    except Exception:
        redis_status = "error"

    cache: SemanticCache = request.app.state.cache

    return HealthResponse(
        status="ok" if redis_status == "ok" else "degraded",
        redis=redis_status,
        workers_active=await producer.active_worker_count(),
        queue_depth=await producer.queue_depth(),
        cache_size=await cache.size(),
        uptime_seconds=round(time.time() - _start_time, 1),
    )


# ── Prometheus Metrics ────────────────────────────────────────────────────────

@router.get("/metrics", summary="Prometheus metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
