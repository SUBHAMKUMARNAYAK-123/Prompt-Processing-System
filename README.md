<<<<<<< HEAD
# 🧠 AI Prompt Queue System

> **Secfolio Assessment — NIST University | April 20, 2026**

A distributed, fault-tolerant system for processing high-volume AI prompt requests with semantic caching, rate limiting, and durable execution.

---

## Architecture Overview

```
┌─────────────┐     ┌──────────────┐     ┌──────────────────┐
│  REST API   │────▶│  Redis Queue │────▶│  Worker Pool     │
│  (FastAPI)  │     │  (Bull/BullMQ│     │  (N parallel)    │
└─────────────┘     └──────────────┘     └──────┬───────────┘
       │                                         │
       │            ┌──────────────┐             │
       └───────────▶│ Semantic     │◀────────────┘
                    │ Cache        │
                    │ (Redis+Embed)│
                    └──────────────┘
                                                 │
                    ┌──────────────┐             │
                    │  LLM Provider│◀────────────┘
                    │  Rate Limiter│  (300 req/min)
                    └──────────────┘
```

### Key Design Decisions

| Concern | Choice | Reason |
|---|---|---|
| Queue | **BullMQ (Redis)** | Durable, supports retries, concurrency, job visibility |
| Semantic Cache | **Redis + OpenAI Embeddings** | Cosine similarity for near-duplicate prompts |
| Rate Limiting | **Token Bucket (Redis)** | Distributed, precise, provider-safe |
| API Framework | **FastAPI** | Async native, OpenAPI docs, type safety |
| Worker Recovery | **BullMQ stalled jobs** | Auto-requeue crashed workers |

---

## Features

- ✅ **REST API** — Submit, poll, and retrieve prompt jobs
- ✅ **Durable Execution** — Jobs survive worker crashes (stalled job detection)
- ✅ **Parallel Processing** — Configurable worker concurrency
- ✅ **Rate Limiting** — Token bucket, 300 req/min, distributed across workers
- ✅ **Semantic Caching** — Embeddings-based similarity (threshold: 0.95 cosine)
- ✅ **Retry Logic** — Exponential backoff on provider errors
- ✅ **Observability** — Prometheus metrics + structured logging

---

## Quick Start

### Prerequisites
- Docker & Docker Compose
- Python 3.11+
- Node.js 18+ (for BullMQ worker)

### 1. Clone & Configure

```bash
git clone https://github.com/your-org/ai-prompt-queue.git
cd ai-prompt-queue
cp .env.example .env
# Edit .env with your OPENAI_API_KEY
```

### 2. Start Everything

```bash
docker-compose up -d
```

This starts:
- FastAPI on `http://localhost:8000`
- Redis on `localhost:6379`
- 4 parallel BullMQ workers
- Bull Board UI on `http://localhost:3001`

### 3. Submit a Prompt

```bash
curl -X POST http://localhost:8000/api/v1/prompts \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Explain quantum entanglement in simple terms",
    "model": "gpt-4o",
    "priority": 1
  }'
```

**Response:**
```json
{
  "job_id": "job_abc123",
  "status": "queued",
  "estimated_wait_seconds": 2,
  "cache_hit": false
}
```

### 4. Poll for Result

```bash
curl http://localhost:8000/api/v1/prompts/job_abc123
```

---

## API Reference

### `POST /api/v1/prompts`
Submit a new prompt job.

**Body:**
```json
{
  "prompt": "string (required)",
  "model": "gpt-4o | gpt-4o-mini | claude-3-5-sonnet (default: gpt-4o-mini)",
  "priority": "0-10 (default: 0)",
  "max_tokens": "integer (default: 1024)",
  "temperature": "0.0-2.0 (default: 0.7)",
  "webhook_url": "string (optional — POSTed to when complete)"
}
```

### `GET /api/v1/prompts/{job_id}`
Get job status and result.

### `GET /api/v1/prompts/{job_id}/stream`
Server-Sent Events stream of job progress.

### `DELETE /api/v1/prompts/{job_id}`
Cancel a queued job.

### `GET /api/v1/metrics`
Prometheus metrics endpoint.

### `GET /api/v1/health`
Health check (Redis + worker status).

---

## Project Structure

```
ai-prompt-queue/
├── src/
│   ├── api/
│   │   ├── main.py            # FastAPI app + lifespan
│   │   ├── routes.py          # All route handlers
│   │   ├── schemas.py         # Pydantic request/response models
│   │   └── middleware.py      # Auth, logging, CORS
│   ├── workers/
│   │   ├── worker.ts          # BullMQ worker (Node.js)
│   │   ├── processor.ts       # Job processing logic
│   │   └── recovery.ts        # Stalled job handler
│   ├── cache/
│   │   ├── semantic_cache.py  # Embedding + cosine similarity cache
│   │   └── key_builder.py     # Cache key generation
│   ├── queue/
│   │   ├── producer.py        # Job enqueue logic
│   │   └── rate_limiter.py    # Token bucket rate limiter
│   └── utils/
│       ├── config.py          # Settings (pydantic-settings)
│       ├── logger.py          # Structured JSON logging
│       └── metrics.py         # Prometheus instrumentation
├── tests/
│   ├── test_api.py
│   ├── test_cache.py
│   ├── test_rate_limiter.py
│   └── test_worker.py
├── scripts/
│   ├── load_test.py           # k6 / locust load test
│   └── seed_cache.py          # Pre-warm semantic cache
├── docs/
│   ├── architecture.md
│   └── runbook.md
├── docker-compose.yml
├── Dockerfile.api
├── Dockerfile.worker
├── .env.example
├── requirements.txt
├── package.json
└── pyproject.toml
```

---

## Configuration

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | — | Required |
| `REDIS_URL` | `redis://localhost:6379` | Redis connection |
| `WORKER_CONCURRENCY` | `4` | Parallel jobs per worker process |
| `RATE_LIMIT_RPM` | `300` | Provider rate limit (requests/minute) |
| `CACHE_SIMILARITY_THRESHOLD` | `0.95` | Cosine similarity for cache hit |
| `CACHE_TTL_SECONDS` | `86400` | Cache entry TTL (24h) |
| `JOB_TIMEOUT_SECONDS` | `60` | Max job execution time |
| `JOB_STALL_INTERVAL_MS` | `30000` | Stalled job check interval |
| `MAX_RETRIES` | `3` | Retry attempts on failure |

---

## Semantic Cache Design

The cache uses OpenAI's `text-embedding-3-small` to embed incoming prompts. On each request:

1. Embed the prompt (256-dim vector)
2. Check Redis vector index (RediSearch) for neighbors with cosine similarity ≥ 0.95
3. **Cache hit** → return stored response instantly, no LLM call
4. **Cache miss** → process normally, store result + embedding

**Similarity threshold 0.95** was chosen to catch near-duplicates (typos, minor rephrasing) while avoiding false positives for semantically different prompts.

---

## Rate Limiter Design

Uses a **token bucket** algorithm implemented in Redis Lua (atomic):

```
Capacity:    300 tokens
Refill rate: 5 tokens/second (= 300/min)
Per request: 1 token consumed
```

If the bucket is empty, the job is **delayed** (not dropped) and retried after the calculated wait time. Workers coordinate via shared Redis state — no single point of contention.

---

## Worker Crash Recovery

BullMQ's stalled job detection handles crashes:
- Workers send heartbeats every `STALL_INTERVAL_MS`
- If a worker dies mid-job, the job is automatically re-queued after `2 × STALL_INTERVAL_MS`
- Jobs are idempotent by design (safe to retry)

---

## Observability

| Metric | Type | Description |
|---|---|---|
| `prompt_jobs_total` | Counter | Total jobs by status |
| `prompt_job_duration_seconds` | Histogram | E2E latency |
| `cache_hits_total` | Counter | Semantic cache hits |
| `rate_limit_delays_total` | Counter | Jobs delayed by rate limiter |
| `worker_active_jobs` | Gauge | Currently processing jobs |
| `llm_tokens_used_total` | Counter | Token consumption |

---

## Load Testing

```bash
pip install locust
locust -f scripts/load_test.py --host=http://localhost:8000
```

Expected throughput: **~300 LLM calls/min**, unlimited cache-hit throughput.

---

## License

MIT
=======
# Prompt-Processing-System
A scalable backend system that processes high-volume AI prompts using asynchronous task queues, built with FastAPI and Celery to ensure reliability and performance.
>>>>>>> a410e551a5b23dd4e46fd8c9694243ee0634bf94
