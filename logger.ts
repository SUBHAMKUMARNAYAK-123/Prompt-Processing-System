/**
 * Structured JSON logger for the worker process.
 * Outputs one JSON object per line for easy ingestion by log aggregators.
 */

type Level = 'debug' | 'info' | 'warn' | 'error';

function log(level: Level, name: string, message: string, extra?: Record<string, unknown>) {
  const entry = {
    timestamp: new Date().toISOString(),
    level,
    logger: name,
    message,
    ...extra,
  };
  const out = level === 'error' || level === 'warn' ? process.stderr : process.stdout;
  out.write(JSON.stringify(entry) + '\n');
}

export function getLogger(name: string) {
  return {
    debug: (msg: string, extra?: Record<string, unknown>) => log('debug', name, msg, extra),
    info:  (msg: string, extra?: Record<string, unknown>) => log('info',  name, msg, extra),
    warn:  (msg: string, extra?: Record<string, unknown>) => log('warn',  name, msg, extra),
    error: (msg: string, extra?: Record<string, unknown>) => log('error', name, msg, extra),
  };
}
