import { describe, expect, it, vi } from 'vitest';
import { fetchHealth, isHealthResponse } from '../src/shared/api/health.js';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('isHealthResponse', () => {
  it('accepts { status: ok } payloads', () => {
    expect(isHealthResponse({ status: 'ok' })).toBe(true);
  });

  it('rejects wrong shapes instead of treating them as healthy', () => {
    expect(isHealthResponse(null)).toBe(false);
    expect(isHealthResponse({ status: 'fine' })).toBe(false);
    expect(isHealthResponse({})).toBe(false);
    expect(isHealthResponse({ status: 'ok', unexpected: true })).toBe(false);
    expect(isHealthResponse('ok')).toBe(false);
  });
});

describe('fetchHealth', () => {
  it('returns the payload for a valid ok response', async () => {
    const fetchFn: typeof fetch = async () => jsonResponse({ status: 'ok' });
    await expect(fetchHealth({ fetchFn, retryDelayMs: 1 })).resolves.toEqual({
      status: 'ok',
    });
  });

  it('fails on non-2xx status instead of reporting success', async () => {
    const fetchFn: typeof fetch = async () => jsonResponse({ status: 'ok' }, 500);
    await expect(
      fetchHealth({ fetchFn, retries: 0, retryDelayMs: 1 }),
    ).rejects.toThrow(/HTTP 500/);
  });

  it('fails on an unexpected response shape', async () => {
    const fetchFn: typeof fetch = async () =>
      jsonResponse({ status: 'unexpected' });
    await expect(
      fetchHealth({ fetchFn, retries: 0, retryDelayMs: 1 }),
    ).rejects.toThrow(/unexpected response shape/);
  });

  it('retries once by default and then succeeds', async () => {
    const fetchFn = vi.fn<typeof fetch>();
    fetchFn.mockRejectedValueOnce(new TypeError('network down'));
    fetchFn.mockResolvedValueOnce(jsonResponse({ status: 'ok' }));
    const result = await fetchHealth({ fetchFn, retryDelayMs: 1 });
    expect(result).toEqual({ status: 'ok' });
    expect(fetchFn).toHaveBeenCalledTimes(2);
    expect(fetchFn.mock.calls[0]?.[0]).toBe('/health');
  });

  it('gives up after the configured retries are exhausted', async () => {
    const fetchFn = vi.fn<typeof fetch>();
    fetchFn.mockRejectedValue(new TypeError('network down'));
    await expect(
      fetchHealth({ fetchFn, retries: 0, retryDelayMs: 1 }),
    ).rejects.toThrow();
    expect(fetchFn).toHaveBeenCalledTimes(1);
  });

  it('propagates abort without retrying', async () => {
    const controller = new AbortController();
    const fetchFn = vi.fn<typeof fetch>();
    fetchFn.mockImplementation(async (_input, init) => {
      controller.abort();
      throw init?.signal?.aborted === true
        ? new DOMException('aborted', 'AbortError')
        : new Error('unexpected call');
    });
    await expect(
      fetchHealth({ fetchFn, signal: controller.signal, retryDelayMs: 1 }),
    ).rejects.toMatchObject({ name: 'AbortError' });
    expect(fetchFn).toHaveBeenCalledTimes(1);
  });
});
