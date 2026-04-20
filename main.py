"""
ai-prompt-queue — FastAPI application entry point.
Handles lifespan (startup/shutdown), middleware, and route registration.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.middleware import LoggingMiddleware, RateLimitHeaderMiddleware
from src.api.routes import router
from src.cache.semantic_cache import SemanticCache
from src.queue.producer import JobProducer
from src.utils.config import settings
from src.utils.logger import get_logger
from src.utils.metrics import setup_metrics

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    logger.info("Starting AI Prompt Queue API", version="1.0.0")

    # Connect to Redis
    app.state.redis = aioredis.from_url(
        settings.REDIS_URL,
        encoding="utf-8",
        decode_responses=True,
    )
    await app.state.redis.ping()
    logger.info("Redis connected", url=settings.REDIS_URL)

    # Initialize semantic cache (creates RediSearch index if not exists)
    app.state.cache = SemanticCache(app.state.redis)
    await app.state.cache.initialize()
    logger.info("Semantic cache initialized")

    # Initialize job producer
    app.state.producer = JobProducer(app.state.redis)
    await app.state.producer.initialize()
    logger.info("Job producer ready")

    yield  # ← application runs here

    # Graceful shutdown
    logger.info("Shutting down...")
    await app.state.producer.close()
    await app.state.redis.aclose()
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI Prompt Queue",
        description="Distributed prompt processing with semantic caching and rate limiting",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Custom middleware
    app.add_middleware(LoggingMiddleware)
    app.add_middleware(RateLimitHeaderMiddleware)

    # Prometheus metrics
    setup_metrics(app)

    # Routes
    app.include_router(router, prefix="/api/v1")

    return app


app = create_app()
