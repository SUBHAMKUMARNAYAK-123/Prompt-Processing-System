"""
Custom FastAPI middleware.
"""
from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from src.utils.logger import get_logger

logger = get_logger("middleware")


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        req_id = str(uuid.uuid4())[:8]
        start  = time.perf_counter()

        response = await call_next(request)

        duration_ms = round((time.perf_counter() - start) * 1000, 1)
        logger.info(
            "HTTP",
            req_id=req_id,
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=duration_ms,
        )
        response.headers["X-Request-Id"] = req_id
        return response


class RateLimitHeaderMiddleware(BaseHTTPMiddleware):
    """Adds X-RateLimit-* headers to every response."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = "300"
        response.headers["X-RateLimit-Window"] = "60s"
        return response
