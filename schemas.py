"""
Request and response schemas for the prompt queue API.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, HttpUrl, field_validator


class ModelChoice(str, Enum):
    GPT4O = "gpt-4o"
    GPT4O_MINI = "gpt-4o-mini"
    CLAUDE_SONNET = "claude-3-5-sonnet-20241022"


class JobStatus(str, Enum):
    QUEUED = "queued"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    CACHE_HIT = "cache_hit"


# ── Request Models ────────────────────────────────────────────────────────────

class PromptRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=32_000, description="The prompt to process")
    model: ModelChoice = Field(default=ModelChoice.GPT4O_MINI, description="LLM model to use")
    priority: int = Field(default=0, ge=0, le=10, description="Job priority (higher = sooner)")
    max_tokens: int = Field(default=1024, ge=1, le=8192)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    system_prompt: Optional[str] = Field(default=None, max_length=4096)
    webhook_url: Optional[str] = Field(default=None, description="POSTed to on completion")
    idempotency_key: Optional[str] = Field(default=None, description="Deduplicate submissions")

    @field_validator("prompt")
    @classmethod
    def strip_prompt(cls, v: str) -> str:
        return v.strip()


# ── Response Models ───────────────────────────────────────────────────────────

class PromptSubmitResponse(BaseModel):
    job_id: str
    status: JobStatus
    cache_hit: bool = False
    estimated_wait_seconds: Optional[float] = None
    result: Optional[str] = None          # Populated immediately on cache hit
    created_at: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    prompt: str
    model: str
    result: Optional[str] = None
    error: Optional[str] = None
    cache_hit: bool = False
    tokens_used: Optional[int] = None
    processing_time_ms: Optional[float] = None
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    attempts: int = 0


class HealthResponse(BaseModel):
    status: str
    redis: str
    workers_active: int
    queue_depth: int
    cache_size: int
    uptime_seconds: float


class MetricsSummary(BaseModel):
    total_jobs: int
    completed_jobs: int
    failed_jobs: int
    cache_hit_rate: float
    avg_latency_ms: float
    current_rpm: float
