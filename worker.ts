/**
 * BullMQ Worker — processes prompt jobs from the queue.
 *
 * Design:
 *  - Pulls jobs from `bull:prompt-queue:wait` via BullMQ
 *  - Enforces rate limits via the shared Redis token bucket
 *  - Calls LLM provider (OpenAI or Anthropic) via provider adapter
 *  - Stores result in Redis job metadata (read by FastAPI /prompts/{id})
 *  - Stores response in semantic cache for future lookups
 *  - Fires webhook if configured
 *  - Stalled job detection: BullMQ auto-requeues if heartbeat stops
 *
 * Crash recovery:
 *  BullMQ moves jobs from `active` back to `wait` after
 *  STALL_INTERVAL_MS * 2 with no heartbeat. Workers are stateless;
 *  jobs are idempotent (LLM calls may repeat, cache will absorb duplicates).
 */

import { Worker, Job, QueueEvents } from 'bullmq';
import Redis from 'ioredis';
import { processPrompt } from './processor';
import { getLogger } from '../utils/logger';

const logger = getLogger('worker');

const QUEUE_NAME = process.env.QUEUE_NAME ?? 'prompt-queue';
const CONCURRENCY  = parseInt(process.env.WORKER_CONCURRENCY ?? '4', 10);
const REDIS_URL    = process.env.REDIS_URL ?? 'redis://localhost:6379';

const connection = new Redis(REDIS_URL, {
  maxRetriesPerRequest: null,   // Required by BullMQ
  enableReadyCheck: false,
});

const worker = new Worker(
  QUEUE_NAME,
  async (job: Job) => {
    const startMs = Date.now();
    logger.info('Processing job', { jobId: job.id, model: job.data.model });

    try {
      const result = await processPrompt(job.data, job);

      // Update metadata in Redis (FastAPI reads this for GET /prompts/:id)
      await connection.hset(`job:meta:${job.id}`, {
        status:            'completed',
        result:            result.response,
        tokens_used:       String(result.tokensUsed ?? ''),
        processing_time_ms: String(Date.now() - startMs),
        completed_at:      String(Date.now() / 1000),
        cache_hit:         result.fromCache ? 'true' : 'false',
      });

      // Fire webhook if configured
      if (job.data.webhook_url) {
        await fireWebhook(job.data.webhook_url, {
          job_id:   job.id,
          status:   'completed',
          response: result.response,
        });
      }

      logger.info('Job completed', {
        jobId:      job.id,
        durationMs: Date.now() - startMs,
        fromCache:  result.fromCache,
      });

      return result;

    } catch (err: any) {
      logger.error('Job failed', { jobId: job.id, error: err.message });

      await connection.hset(`job:meta:${job.id}`, {
        status:         'failed',
        error:          err.message,
        completed_at:   String(Date.now() / 1000),
      });

      throw err;   // BullMQ will retry based on job opts.attempts
    }
  },
  {
    connection,
    concurrency: CONCURRENCY,
    stalledInterval: parseInt(process.env.JOB_STALL_INTERVAL_MS ?? '30000', 10),
    lockDuration:    60_000,    // Must complete within 60s or considered stalled
    limiter: {
      max:      10,
      duration: 1000,           // Client-side throttle: max 10 active poll loops/sec
    },
  }
);

// ── Event Listeners ──────────────────────────────────────────────────────────

worker.on('active', (job) => {
  connection.hset(`job:meta:${job.id}`, {
    status:     'active',
    started_at: String(Date.now() / 1000),
    attempts:   String(job.attemptsMade + 1),
  });
});

worker.on('failed', (job, err) => {
  if (job && job.attemptsMade >= (job.opts.attempts ?? 1)) {
    logger.error('Job permanently failed', { jobId: job.id, error: err.message });
  }
});

worker.on('stalled', (jobId) => {
  logger.warn('Job stalled — will be requeued', { jobId });
});

worker.on('error', (err) => {
  logger.error('Worker error', { error: err.message });
});

// ── Graceful Shutdown ─────────────────────────────────────────────────────────

async function shutdown(signal: string) {
  logger.info(`Received ${signal}, shutting down worker gracefully...`);
  await worker.close();
  await connection.quit();
  process.exit(0);
}

process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('SIGINT',  () => shutdown('SIGINT'));

// ── Webhook Utility ───────────────────────────────────────────────────────────

async function fireWebhook(url: string, payload: object): Promise<void> {
  try {
    const res = await fetch(url, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(payload),
      signal:  AbortSignal.timeout(5000),
    });
    if (!res.ok) {
      logger.warn('Webhook returned non-2xx', { url, status: res.status });
    }
  } catch (err: any) {
    logger.warn('Webhook failed', { url, error: err.message });
  }
}

logger.info('Worker started', {
  queue:       QUEUE_NAME,
  concurrency: CONCURRENCY,
  redisUrl:    REDIS_URL,
});
