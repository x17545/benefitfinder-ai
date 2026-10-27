import type { HealthResponse } from './generated/health.js';
import { HEALTH_PATH } from '../config/api.js';

export type { HealthResponse };

export interface FetchHealthOptions {
  /** AbortSignal for unmount and request cleanup. */
  signal?: AbortSignal;
  /** Extra retries after the first attempt. Defaults to 1. */
  retries?: number;
  /** Wait between attempts in milliseconds. Defaults to 500. */
  retryDelayMs?: number;
  /** Injectable fetch for tests. Defaults to the global fetch. */
  fetchFn?: typeof fetch;
  /** Request path. Defaults to /health. */
  path?: string;
}

/**
 * Validate an unknown payload as HealthResponse.
 * Anything else is a failure and is never reported as healthy.
 */
export function isHealthResponse(value: unknown): value is HealthResponse {
  if (typeof value !== 'object' || value === null) {
    return false;
  }
  return Object.keys(value).length === 1 && (value as { status?: unknown }).status === 'ok';
}

function delay(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const cleanup = (): void => {
      signal?.removeEventListener('abort', onAbort);
    };
    const onAbort = (): void => {
      clearTimeout(timer);
      cleanup();
      reject(signal?.reason ?? new Error('aborted'));
    };
    const timer = setTimeout(() => {
      cleanup();
      resolve();
    }, ms);
    if (signal?.aborted === true) {
      onAbort();
      return;
    }
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}

function toError(error: unknown): Error {
  if (error instanceof Error) {
    return error;
  }
  return new Error('health check failed: unknown error');
}

/**
 * GET the backend health endpoint and validate the payload.
 * Throws on network failure, non-2xx status, or an unexpected shape.
 */
export async function fetchHealth(
  options: FetchHealthOptions = {},
): Promise<HealthResponse> {
  const {
    signal,
    retries = 1,
    retryDelayMs = 500,
    fetchFn = fetch,
    path = HEALTH_PATH,
  } = options;

  let attempt = 0;
  for (;;) {
    try {
      const response = await fetchFn(path, { signal });
      if (!response.ok) {
        throw new Error(
          `health check failed: HTTP ${response.status.toString()}`,
        );
      }
      const body: unknown = await response.json();
      if (!isHealthResponse(body)) {
        throw new Error('health check failed: unexpected response shape');
      }
      return body;
    } catch (error) {
      if (signal?.aborted === true) {
        throw toError(error);
      }
      if (attempt >= retries) {
        throw toError(error);
      }
      attempt += 1;
      await delay(retryDelayMs, signal);
    }
  }
}
