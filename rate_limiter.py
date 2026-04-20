"""
Distributed token bucket rate limiter backed by Redis.

Token Bucket design:
  - Capacity:    300 tokens (= max burst = 1 full minute of allowance)
  - Refill rate: 5 tokens/second  (300 / 60)
  - Cost:        1 token per LLM request

Implemented as a Lua script for atomicity — no race conditions
across multiple worker processes sharing the same Redis.

If bucket is empty → returns wait_ms > 0, worker should sleep.
"""
from __future__ import annotations

import time
from typing import Tuple

from src.utils.config import settings
from src.utils.logger import get_logger
from src.utils.metrics import RATE_LIMIT_DELAYS

logger = get_logger(__name__)

BUCKET_KEY = "ratelimit:provider:bucket"
LAST_REFILL_KEY = "ratelimit:provider:last_refill"

# Lua script: atomically consume 1 token, refill based on elapsed time
_CONSUME_LUA = """
local bucket_key     = KEYS[1]
local last_key       = KEYS[2]
local capacity       = tonumber(ARGV[1])
local refill_rate    = tonumber(ARGV[2])   -- tokens per second
local now            = tonumber(ARGV[3])   -- current time (milliseconds)
local cost           = tonumber(ARGV[4])

-- Get current state
local tokens     = tonumber(redis.call('GET', bucket_key) or capacity)
local last_refill = tonumber(redis.call('GET', last_key) or now)

-- Refill bucket
local elapsed = (now - last_refill) / 1000.0   -- seconds
local refilled = math.min(capacity, tokens + elapsed * refill_rate)

-- Try to consume
if refilled >= cost then
    -- Success
    redis.call('SET', bucket_key, refilled - cost)
    redis.call('SET', last_key, now)
    redis.call('EXPIRE', bucket_key, 120)
    redis.call('EXPIRE', last_key, 120)
    return {1, 0}   -- {allowed, wait_ms}
else
    -- Not enough tokens; calculate wait
    local deficit   = cost - refilled
    local wait_ms   = math.ceil((deficit / refill_rate) * 1000)
    redis.call('SET', bucket_key, refilled)
    redis.call('SET', last_key, now)
    redis.call('EXPIRE', bucket_key, 120)
    redis.call('EXPIRE', last_key, 120)
    return {0, wait_ms}
end
"""


class RateLimiter:
    def __init__(self, redis):
        self.redis = redis
        self.capacity = settings.RATE_LIMIT_RPM
        self.refill_rate = self.capacity / 60.0   # tokens/second
        self._script = None

    async def _load_script(self):
        if not self._script:
            self._script = await self.redis.script_load(_CONSUME_LUA)

    async def acquire(self) -> Tuple[bool, int]:
        """
        Attempt to consume one token.
        Returns (allowed: bool, wait_ms: int).
        If not allowed, caller should sleep wait_ms before retrying.
        """
        await self._load_script()
        now_ms = int(time.time() * 1000)

        result = await self.redis.evalsha(
            self._script,
            2,                          # numkeys
            BUCKET_KEY,
            LAST_REFILL_KEY,
            self.capacity,              # ARGV[1]
            self.refill_rate,           # ARGV[2]
            now_ms,                     # ARGV[3]
            1,                          # ARGV[4] cost
        )

        allowed = bool(result[0])
        wait_ms = int(result[1])

        if not allowed:
            RATE_LIMIT_DELAYS.inc()
            logger.debug("Rate limit hit", wait_ms=wait_ms)

        return allowed, wait_ms

    async def wait_and_acquire(self, max_wait_ms: int = 60_000) -> bool:
        """
        Block until a token is available (up to max_wait_ms).
        Returns True if acquired, False if timed out.
        """
        import asyncio

        waited = 0
        while waited < max_wait_ms:
            allowed, wait_ms = await self.acquire()
            if allowed:
                return True
            sleep_ms = min(wait_ms, 500)   # Cap sleep to 500ms for responsiveness
            await asyncio.sleep(sleep_ms / 1000)
            waited += sleep_ms

        logger.warning("Rate limiter timed out", max_wait_ms=max_wait_ms)
        return False

    async def current_tokens(self) -> float:
        raw = await self.redis.get(BUCKET_KEY)
        return float(raw) if raw else float(self.capacity)
