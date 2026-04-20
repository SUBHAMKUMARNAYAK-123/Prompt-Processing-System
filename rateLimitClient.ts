/**
 * Rate limit client — calls the Redis Lua token bucket from the worker side.
 * Mirrors the Python implementation in src/queue/rate_limiter.py.
 */

import Redis from 'ioredis';

const BUCKET_KEY      = 'ratelimit:provider:bucket';
const LAST_REFILL_KEY = 'ratelimit:provider:last_refill';
const CAPACITY        = parseInt(process.env.RATE_LIMIT_RPM ?? '300', 10);
const REFILL_RATE     = CAPACITY / 60.0;   // tokens per second
const MAX_WAIT_MS     = 60_000;

const CONSUME_LUA = `
local bucket_key     = KEYS[1]
local last_key       = KEYS[2]
local capacity       = tonumber(ARGV[1])
local refill_rate    = tonumber(ARGV[2])
local now            = tonumber(ARGV[3])
local cost           = tonumber(ARGV[4])

local tokens      = tonumber(redis.call('GET', bucket_key) or capacity)
local last_refill = tonumber(redis.call('GET', last_key) or now)

local elapsed  = (now - last_refill) / 1000.0
local refilled = math.min(capacity, tokens + elapsed * refill_rate)

if refilled >= cost then
    redis.call('SET', bucket_key, refilled - cost)
    redis.call('SET', last_key, now)
    redis.call('EXPIRE', bucket_key, 120)
    redis.call('EXPIRE', last_key, 120)
    return {1, 0}
else
    local deficit = cost - refilled
    local wait_ms = math.ceil((deficit / refill_rate) * 1000)
    redis.call('SET', bucket_key, refilled)
    redis.call('SET', last_key, now)
    redis.call('EXPIRE', bucket_key, 120)
    redis.call('EXPIRE', last_key, 120)
    return {0, wait_ms}
end
`;

export async function acquireRateLimitToken(
  redis: Redis,
  maxWaitMs = MAX_WAIT_MS,
): Promise<boolean> {
  const sha = await redis.script('load', CONSUME_LUA) as string;
  let waited = 0;

  while (waited < maxWaitMs) {
    const nowMs = Date.now();
    const result = await redis.evalsha(
      sha, 2,
      BUCKET_KEY, LAST_REFILL_KEY,
      String(CAPACITY), String(REFILL_RATE), String(nowMs), '1',
    ) as [number, number];

    if (result[0] === 1) return true;

    const sleepMs = Math.min(result[1], 500);
    await sleep(sleepMs);
    waited += sleepMs;
  }

  return false;
}

function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}
