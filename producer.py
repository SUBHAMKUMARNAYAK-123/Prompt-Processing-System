"""
Job producer — enqueues prompt jobs into BullMQ via Redis.

BullMQ stores jobs in Redis sorted sets:
  bull:{queue}:wait      — queued jobs (FIFO within priority)
  bull:{queue}:active    — jobs being processed
  bull:{queue}:completed — completed jobs
  bull:{queue}:failed    — permanently failed jobs

We write job metadata to a separate hash for fast status lookups
without parsing BullMQ's internal format.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Optional

from src.utils.config import settings
from src.utils.logger import get_logger
from src.utils.metrics import JOBS_TOTAL

logger = get_logger(__name__)

QUEUE_NAME = settings.QUEUE_NAME
JOB_META_PREFIX = "job:meta:"
IDEMPOTENCY_PREFIX = "job:idem:"


class JobProducer:
    def __init__(self, redis):
        self.redis = redis

    async def initialize(self):
        """Ensure queue structures exist (BullMQ creates them lazily)."""
        pass  # BullMQ handles this from the worker side

    async def enqueue(
        self,
        prompt: str,
        model: str,
        priority: int = 0,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        system_prompt: Optional[str] = None,
        webhook_url: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> str:
        job_id = f"job_{uuid.uuid4().hex[:12]}"
        now = time.time()

        job_data = {
            "job_id": job_id,
            "prompt": prompt,
            "model": model,
            "priority": priority,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system_prompt": system_prompt or "",
            "webhook_url": webhook_url or "",
            "created_at": now,
        }

        # BullMQ job payload (compatible with BullMQ Node.js worker)
        bull_job = {
            "id": job_id,
            "name": "process_prompt",
            "data": job_data,
            "opts": {
                "priority": priority,
                "attempts": settings.MAX_RETRIES,
                "backoff": {
                    "type": "exponential",
                    "delay": 2000,   # 2s base, doubles each retry
                },
                "removeOnComplete": False,
                "removeOnFail": False,
            },
            "timestamp": int(now * 1000),
            "processedOn": None,
            "finishedOn": None,
        }

        async with self.redis.pipeline(transaction=True) as pipe:
            # Write to BullMQ wait set (score = -priority for max-heap via ZRANGEBYSCORE)
            pipe.zadd(
                f"bull:{QUEUE_NAME}:wait",
                {json.dumps(bull_job): -priority},
            )

            # Write metadata for fast lookup
            pipe.hset(
                f"{JOB_META_PREFIX}{job_id}",
                mapping={
                    "job_id": job_id,
                    "status": "queued",
                    "prompt": prompt[:200],
                    "model": model,
                    "priority": str(priority),
                    "attempts": "0",
                    "cache_hit": "false",
                    "created_at": str(now),
                    "started_at": "",
                    "completed_at": "",
                    "result": "",
                    "error": "",
                    "tokens_used": "",
                    "processing_time_ms": "",
                },
            )
            pipe.expire(f"{JOB_META_PREFIX}{job_id}", 86400 * 7)  # 7-day TTL

            # Idempotency key → job_id mapping
            if idempotency_key:
                pipe.set(
                    f"{IDEMPOTENCY_PREFIX}{idempotency_key}",
                    job_id,
                    ex=3600,   # 1-hour idempotency window
                )

            await pipe.execute()

        JOBS_TOTAL.labels(status="queued").inc()
        logger.info("Job enqueued", job_id=job_id, model=model, priority=priority)
        return job_id

    async def get_job(self, job_id: str) -> Optional[dict]:
        raw = await self.redis.hgetall(f"{JOB_META_PREFIX}{job_id}")
        if not raw:
            return None

        return {
            "job_id": raw.get("job_id"),
            "status": raw.get("status", "queued"),
            "prompt": raw.get("prompt", ""),
            "model": raw.get("model", ""),
            "result": raw.get("result") or None,
            "error": raw.get("error") or None,
            "cache_hit": raw.get("cache_hit") == "true",
            "tokens_used": int(raw["tokens_used"]) if raw.get("tokens_used") else None,
            "processing_time_ms": float(raw["processing_time_ms"]) if raw.get("processing_time_ms") else None,
            "created_at": raw.get("created_at", ""),
            "started_at": raw.get("started_at") or None,
            "completed_at": raw.get("completed_at") or None,
            "attempts": int(raw.get("attempts", 0)),
        }

    async def get_by_idempotency_key(self, key: str) -> Optional[dict]:
        job_id = await self.redis.get(f"{IDEMPOTENCY_PREFIX}{key}")
        if job_id:
            return await self.get_job(job_id)
        return None

    async def cancel_job(self, job_id: str) -> bool:
        meta_key = f"{JOB_META_PREFIX}{job_id}"
        status = await self.redis.hget(meta_key, "status")

        if status not in ("queued",):
            return False

        # Remove from BullMQ wait set — this is safe because active jobs can't be cancelled
        async with self.redis.pipeline() as pipe:
            # Find and remove from wait set (scan since we stored by value)
            wait_key = f"bull:{QUEUE_NAME}:wait"
            members = await self.redis.zrange(wait_key, 0, -1)
            for m in members:
                try:
                    data = json.loads(m)
                    if data.get("id") == job_id:
                        pipe.zrem(wait_key, m)
                        break
                except json.JSONDecodeError:
                    continue

            pipe.hset(meta_key, "status", "cancelled")
            await pipe.execute()

        JOBS_TOTAL.labels(status="cancelled").inc()
        return True

    async def queue_depth(self) -> int:
        return await self.redis.zcard(f"bull:{QUEUE_NAME}:wait") or 0

    async def active_worker_count(self) -> int:
        return await self.redis.zcard(f"bull:{QUEUE_NAME}:active") or 0

    async def close(self):
        pass  # Redis connection closed by the app lifespan
