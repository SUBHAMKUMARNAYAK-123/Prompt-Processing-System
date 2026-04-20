# Architecture Deep Dive

## Overview

The system is split into two independent processes that communicate exclusively through Redis:

1. **FastAPI (Python)** — HTTP interface, semantic cache gatekeeper, job producer
2. **BullMQ Worker (Node.js)** — Job consumer, LLM caller, rate limit enforcer

This split allows each layer to scale independently.

---

## Data Flow

### Unhappy Path (cache miss, new prompt)

```
Client
  │
  │  POST /api/v1/prompts
  ▼
FastAPI
  ├── Embed prompt (OpenAI text-embedding-3-small)
  ├── RediSearch KNN → similarity < 0.95 → MISS
  ├── Write job to bull:prompt-queue:wait (sorted set, score = -priority)
  ├── Write job metadata to job:meta:{id} (hash)
  └── Return 202 { job_id, status: "queued" }

  [async]

BullMQ Worker (picks up from sorted set)
  ├── Acquire rate-limit token (Redis Lua token bucket)
  │     └── If bucket empty → sleep until refilled
  ├── Mark job:meta:{id}.status = "active"
  ├── Call LLM provider (OpenAI / Anthropic)
  ├── Mark job:meta:{id}.status = "completed", store result
  ├── Publish to cache:store channel (Python subscribes, stores embedding)
  └── Fire webhook if configured

Client
  │
  │  GET /api/v1/prompts/{job_id}   (polling)
  ▼
FastAPI
  └── Read job:meta:{id} → return status + result
```

### Happy Path (cache hit)

```
Client
  │
  │  POST /api/v1/prompts
  ▼
FastAPI
  ├── Embed prompt
  ├── RediSearch KNN → similarity ≥ 0.95 → HIT
  └── Return 202 { status: "cache_hit", result: "..." }   ← instant, no LLM call
```

---

## Redis Data Structures

| Key Pattern | Type | Purpose |
|---|---|---|
| `bull:prompt-queue:wait` | Sorted Set | Queued jobs (score = -priority) |
| `bull:prompt-queue:active` | Sorted Set | Jobs being processed |
| `bull:prompt-queue:completed` | Sorted Set | Completed jobs (BullMQ managed) |
| `bull:prompt-queue:failed` | Sorted Set | Failed jobs |
| `job:meta:{job_id}` | Hash | Job metadata (status, result, timestamps) |
| `job:idem:{key}` | String | Idempotency key → job_id mapping |
| `cache:prompt:{job_id}` | Hash | Prompt + embedding + response |
| `ratelimit:provider:bucket` | String | Current token count |
| `ratelimit:provider:last_refill` | String | Last refill timestamp (ms) |

---

## Semantic Cache: Threshold Selection

The `CACHE_SIMILARITY_THRESHOLD=0.95` value balances:

- **Too low (< 0.90)**: False positives — different questions get same answer
- **Too high (1.0)**: Only exact duplicates match; defeats the purpose
- **0.95**: Catches rephrasing, minor typos, word order changes while avoiding cross-topic matches

### Examples at 0.95

| Prompt A | Prompt B | Similarity | Hit? |
|---|---|---|---|
| "What is Python?" | "What is Python?" | 1.00 | ✅ |
| "What is Python?" | "Can you explain Python?" | 0.97 | ✅ |
| "What is Python?" | "Explain Python programming language" | 0.95 | ✅ |
| "What is Python?" | "What is JavaScript?" | 0.71 | ❌ |
| "What is Python?" | "How does a car engine work?" | 0.22 | ❌ |

---

## Rate Limiter: Token Bucket vs Sliding Window

We chose **token bucket** over sliding window log because:

- Allows **bursting** up to capacity (useful for batch processing)
- Simple to implement atomically in Lua
- Shared state in Redis works naturally across multiple worker processes
- The `wait_ms` return value lets workers **delay gracefully** rather than drop jobs

### Comparison

| Algorithm | Burst | Accuracy | Distributed | Complexity |
|---|---|---|---|---|
| Token Bucket | ✅ Yes | Good | ✅ Easy | Low |
| Sliding Window Log | ❌ No | Exact | Medium | High |
| Fixed Window Counter | ✅ Yes | Poor (boundary burst) | ✅ Easy | Very Low |

---

## Worker Crash Recovery

BullMQ implements stalled job detection:

1. When a worker picks up a job, it moves it to `bull:prompt-queue:active`
2. The worker sends a heartbeat (lock refresh) every `lockDuration/2` ms
3. A separate check runs every `stalledInterval` ms
4. If a job's lock has expired → it's moved back to `wait` and requeued

**Why jobs are safe to retry:**
- LLM calls are not side-effectful (idempotent at the prompt level)
- Cache stores happen after LLM call; a re-run just overwrites with same content
- Webhooks may fire twice — consumers should handle this (use `job_id` for dedup)

---

## Scaling Considerations

| Bottleneck | Solution |
|---|---|
| API throughput | Add more FastAPI replicas (stateless) |
| Queue processing | Add more worker replicas (`docker-compose up --scale worker=N`) |
| Rate limit pressure | Workers automatically coordinate via shared Redis bucket |
| Cache cold start | Run `scripts/seed_cache.py` on deploy |
| Redis single point | Use Redis Cluster or Redis Sentinel for HA |

---

## Security

- API keys supported via `X-API-Key` header (enable with `REQUIRE_API_KEY=true`)
- All secrets via environment variables, never hardcoded
- Non-root Docker users in both containers
- Prompt length capped at 32,000 characters to prevent abuse
- Webhook URLs validated (no SSRF mitigation beyond URL validation — add allowlist in production)
