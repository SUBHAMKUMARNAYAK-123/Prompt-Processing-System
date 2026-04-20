"""
Application settings via pydantic-settings.
All values can be overridden by environment variables or .env file.
"""
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # LLM Provider
    OPENAI_API_KEY: str = Field(default="", description="OpenAI API key")
    ANTHROPIC_API_KEY: str = Field(default="", description="Anthropic API key")

    # Redis
    REDIS_URL: str = Field(default="redis://localhost:6379")

    # Queue
    QUEUE_NAME: str = Field(default="prompt-queue")
    WORKER_CONCURRENCY: int = Field(default=4)
    MAX_RETRIES: int = Field(default=3)
    JOB_TIMEOUT_SECONDS: int = Field(default=60)
    JOB_STALL_INTERVAL_MS: int = Field(default=30_000)

    # Rate limiting
    RATE_LIMIT_RPM: int = Field(default=300, description="Max LLM requests per minute")

    # Semantic cache
    CACHE_SIMILARITY_THRESHOLD: float = Field(
        default=0.95,
        description="Minimum cosine similarity to count as cache hit (0-1)",
    )
    CACHE_TTL_SECONDS: int = Field(default=86_400, description="Cache entry TTL (24h)")

    # API
    CORS_ORIGINS: list[str] = Field(default=["*"])
    API_KEY_HEADER: str = Field(default="X-API-Key")
    REQUIRE_API_KEY: bool = Field(default=False)

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


settings = Settings()
