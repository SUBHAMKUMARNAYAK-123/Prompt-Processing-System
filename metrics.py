"""
Prometheus instrumentation for the prompt queue API.
"""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram
from prometheus_fastapi_instrumentator import Instrumentator

# Job counters
JOBS_TOTAL = Counter(
    "prompt_jobs_total",
    "Total prompt jobs submitted",
    ["status"],   # queued | completed | failed | cancelled | cache_hit
)

# Cache metrics
CACHE_HITS = Counter("cache_hits_total", "Semantic cache hits")
CACHE_MISSES = Counter("cache_misses_total", "Semantic cache misses")

# Rate limiter
RATE_LIMIT_DELAYS = Counter(
    "rate_limit_delays_total",
    "Number of times jobs were delayed by rate limiter",
)

# Latency histogram
JOB_DURATION = Histogram(
    "prompt_job_duration_seconds",
    "End-to-end job processing latency",
    buckets=[0.1, 0.5, 1, 2, 5, 10, 20, 30, 60],
)

# Queue depth gauge (scraped by workers publishing to Redis pubsub)
QUEUE_DEPTH = Gauge("prompt_queue_depth", "Current number of queued jobs")
ACTIVE_WORKERS = Gauge("worker_active_jobs", "Jobs currently being processed")

# Token usage
TOKENS_USED = Counter(
    "llm_tokens_used_total",
    "Total LLM tokens consumed",
    ["model"],
)


def setup_metrics(app):
    """Attach prometheus-fastapi-instrumentator to the app."""
    Instrumentator(
        should_group_status_codes=True,
        excluded_handlers=["/metrics", "/health"],
    ).instrument(app).expose(app, endpoint="/metrics-http")
    # Note: Our custom /metrics route serves generate_latest() directly
