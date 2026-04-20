/**
 * Job processor — the core business logic for executing a prompt.
 *
 * Steps:
 *  1. Acquire rate-limit token (block if bucket empty)
 *  2. Call LLM provider
 *  3. Publish result to semantic cache
 */

import Redis from 'ioredis';
import OpenAI from 'openai';
import Anthropic from '@anthropic-ai/sdk';
import { Job } from 'bullmq';
import { acquireRateLimitToken } from './rateLimitClient';
import { getLogger } from '../utils/logger';

const logger = getLogger('processor');

const openai    = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });
const anthropic = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });
const redis     = new Redis(process.env.REDIS_URL ?? 'redis://localhost:6379', {
  maxRetriesPerRequest: null,
  enableReadyCheck: false,
});

export interface ProcessResult {
  response:   string;
  tokensUsed: number | null;
  fromCache:  boolean;
}

export async function processPrompt(
  data: {
    job_id:        string;
    prompt:        string;
    model:         string;
    max_tokens:    number;
    temperature:   number;
    system_prompt: string;
  },
  job: Job,
): Promise<ProcessResult> {
  const { job_id, prompt, model, max_tokens, temperature, system_prompt } = data;

  // 1. Acquire rate-limit token — blocks until available (up to 60s)
  logger.debug('Acquiring rate limit token', { jobId: job_id });
  const acquired = await acquireRateLimitToken(redis);
  if (!acquired) {
    throw new Error('Rate limiter timed out after 60 seconds');
  }

  // Update job progress (visible in Bull Board)
  await job.updateProgress(25);

  // 2. Call LLM
  logger.info('Calling LLM', { model, jobId: job_id });
  let response: string;
  let tokensUsed: number | null = null;

  if (model.startsWith('gpt-') || model.startsWith('o')) {
    const result = await callOpenAI({ model, prompt, system_prompt, max_tokens, temperature });
    response  = result.response;
    tokensUsed = result.tokensUsed;
  } else if (model.startsWith('claude-')) {
    const result = await callAnthropic({ model, prompt, system_prompt, max_tokens, temperature });
    response  = result.response;
    tokensUsed = result.tokensUsed;
  } else {
    throw new Error(`Unknown model: ${model}`);
  }

  await job.updateProgress(75);

  // 3. Store in semantic cache (fire-and-forget, don't block job completion)
  storeCacheAsync(job_id, prompt, model, response);

  await job.updateProgress(100);

  return { response, tokensUsed, fromCache: false };
}


// ── LLM Provider Adapters ─────────────────────────────────────────────────────

async function callOpenAI(params: {
  model:         string;
  prompt:        string;
  system_prompt: string;
  max_tokens:    number;
  temperature:   number;
}): Promise<{ response: string; tokensUsed: number }> {
  const messages: OpenAI.ChatCompletionMessageParam[] = [];

  if (params.system_prompt) {
    messages.push({ role: 'system', content: params.system_prompt });
  }
  messages.push({ role: 'user', content: params.prompt });

  const completion = await openai.chat.completions.create({
    model:       params.model,
    messages,
    max_tokens:  params.max_tokens,
    temperature: params.temperature,
  });

  return {
    response:   completion.choices[0]?.message?.content ?? '',
    tokensUsed: completion.usage?.total_tokens ?? 0,
  };
}

async function callAnthropic(params: {
  model:         string;
  prompt:        string;
  system_prompt: string;
  max_tokens:    number;
  temperature:   number;
}): Promise<{ response: string; tokensUsed: number }> {
  const message = await anthropic.messages.create({
    model:      params.model,
    max_tokens: params.max_tokens,
    system:     params.system_prompt || undefined,
    messages:   [{ role: 'user', content: params.prompt }],
  });

  const text = message.content
    .filter(b => b.type === 'text')
    .map(b => (b as Anthropic.TextBlock).text)
    .join('');

  return {
    response:   text,
    tokensUsed: (message.usage.input_tokens + message.usage.output_tokens),
  };
}


// ── Cache Store (fire and forget) ─────────────────────────────────────────────

function storeCacheAsync(job_id: string, prompt: string, model: string, response: string) {
  // Signal Python API to store in semantic cache via a Redis pub/sub message
  redis.publish('cache:store', JSON.stringify({ job_id, prompt, model, response }))
    .catch(err => logger.warn('Cache publish failed', { error: err.message }));
}
