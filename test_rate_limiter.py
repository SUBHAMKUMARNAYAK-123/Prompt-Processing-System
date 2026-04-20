"""
Tests for the Redis token bucket rate limiter.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.queue.rate_limiter import RateLimiter


@pytest.fixture
def mock_redis():
    r = AsyncMock()
    r.script_load = AsyncMock(return_value="sha123")
    return r


@pytest.fixture
def limiter(mock_redis):
    rl = RateLimiter(mock_redis)
    rl.capacity = 300
    rl.refill_rate = 5.0
    return rl


class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_acquire_allowed(self, limiter, mock_redis):
        mock_redis.evalsha = AsyncMock(return_value=[1, 0])

        allowed, wait_ms = await limiter.acquire()

        assert allowed is True
        assert wait_ms == 0

    @pytest.mark.asyncio
    async def test_acquire_denied_returns_wait(self, limiter, mock_redis):
        mock_redis.evalsha = AsyncMock(return_value=[0, 1200])  # 1.2s wait

        allowed, wait_ms = await limiter.acquire()

        assert allowed is False
        assert wait_ms == 1200

    @pytest.mark.asyncio
    async def test_wait_and_acquire_succeeds_on_retry(self, limiter, mock_redis):
        # First call: denied, wait 100ms. Second call: allowed.
        mock_redis.evalsha = AsyncMock(side_effect=[
            [0, 100],
            [1, 0],
        ])

        result = await limiter.wait_and_acquire(max_wait_ms=5000)
        assert result is True

    @pytest.mark.asyncio
    async def test_wait_and_acquire_times_out(self, limiter, mock_redis):
        # Always denied
        mock_redis.evalsha = AsyncMock(return_value=[0, 500])

        result = await limiter.wait_and_acquire(max_wait_ms=200)
        assert result is False

    @pytest.mark.asyncio
    async def test_loads_script_once(self, limiter, mock_redis):
        mock_redis.evalsha = AsyncMock(return_value=[1, 0])

        await limiter.acquire()
        await limiter.acquire()
        await limiter.acquire()

        # Script should only be loaded once
        mock_redis.script_load.assert_called_once()

    @pytest.mark.asyncio
    async def test_current_tokens(self, limiter, mock_redis):
        mock_redis.get = AsyncMock(return_value="247.5")

        tokens = await limiter.current_tokens()
        assert tokens == pytest.approx(247.5)

    @pytest.mark.asyncio
    async def test_current_tokens_defaults_to_capacity(self, limiter, mock_redis):
        mock_redis.get = AsyncMock(return_value=None)

        tokens = await limiter.current_tokens()
        assert tokens == 300
