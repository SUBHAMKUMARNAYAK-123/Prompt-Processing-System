# Operations Runbook

## Common Tasks

### Start the system
```bash
docker-compose up -d
docker-compose ps          # Verify all services running
curl http://localhost:8000/api/v1/health
```

### Scale workers up
```bash
docker-compose up -d --scale worker=4
```

### View queue depth and job stats
```bash
# Via API
curl http://localhost:8000/api/v1/health | jq .

# Via Bull Board UI
open http://localhost:3001

# Via Redis CLI
redis-cli ZCARD bull:prompt-queue:wait      # Queued jobs
redis-cli ZCARD bull:prompt-queue:active    # Active jobs
redis-cli ZCARD bull:prompt-queue:failed    # Failed jobs
```

### Check rate limiter state
```bash
redis-cli GET ratelimit:provider:bucket     # Current token count
```

### Flush the semantic cache
```bash
redis-cli KEYS "cache:prompt:*" | xargs redis-cli DEL
redis-cli FT.DROPINDEX prompt_cache DD      # Drop + delete all docs
# Restart API to recreate index
docker-compose restart api
```

### Retry all failed jobs
Bull Board UI → Failed tab → Retry All

Or via Redis:
```bash
# Move all failed jobs back to wait
redis-cli ZRANGEBYSCORE bull:prompt-queue:failed -inf +inf | \
  xargs -I {} redis-cli ZADD bull:prompt-queue:wait 0 {}
```

---

## Monitoring

### Key Metrics to Watch

| Metric | Alert Threshold | Action |
|---|---|---|
| `prompt_queue_depth` | > 500 | Scale up workers |
| `cache_hit_rate` | < 10% | Check embedding service |
| `rate_limit_delays_total` (rate) | > 50/min | Review provider quota |
| `prompt_job_duration_seconds` p99 | > 30s | Check LLM provider latency |
| `worker_active_jobs` | 0 for > 60s with queue > 0 | Worker crash — restart |

### Prometheus queries
```promql
# Cache hit rate
rate(cache_hits_total[5m]) / (rate(cache_hits_total[5m]) + rate(cache_misses_total[5m]))

# Job throughput (jobs/minute)
rate(prompt_jobs_total{status="completed"}[1m]) * 60

# p99 latency
histogram_quantile(0.99, rate(prompt_job_duration_seconds_bucket[5m]))

# Rate limit pressure
rate(rate_limit_delays_total[1m])
```

---

## Troubleshooting

### Workers not picking up jobs
1. Check Redis connection: `docker-compose logs worker | grep -i redis`
2. Check queue: `redis-cli ZCARD bull:prompt-queue:wait`
3. Check active lock keys: `redis-cli KEYS "bull:prompt-queue:*:lock"`
4. Restart workers: `docker-compose restart worker`

### High rate of failed jobs
1. Check LLM provider API key: `docker-compose logs worker | grep -i "api key"`
2. Check provider status page
3. Check retry configuration in `.env` (`MAX_RETRIES`)

### Cache always missing
1. Check RediSearch index: `redis-cli FT.INFO prompt_cache`
2. Verify OpenAI key has access to embeddings: `openai api embeddings.create -m text-embedding-3-small -i test`
3. Check embedding error logs: `docker-compose logs api | grep -i embed`

### Memory pressure on Redis
1. Check memory: `redis-cli INFO memory | grep used_memory_human`
2. Reduce `CACHE_TTL_SECONDS` in `.env`
3. Add Redis `maxmemory` and `maxmemory-policy allkeys-lru` to redis config
