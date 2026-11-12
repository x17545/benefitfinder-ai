import { afterEach, describe, expect, it, vi } from 'vitest';
import fixture from '../../../contracts/fixtures/meta.json';
import { fetchServiceMeta, isServiceMeta } from '../src/shared/api/meta.ts';

afterEach(() => { vi.useRealTimers(); });

describe('public metadata validation', () => {
  it.each([
    fixture, { ...fixture, virtual_data: false },
    { ...fixture, virtual_data: true, base_rate_status: 'unknown' },
    { ...fixture, base_rate: null, base_rate_status: 'unknown' },
    { ...fixture, base_rate: 0, daily_remaining: 0 },
    { ...fixture, data_updated_at: '2028-02-29T23:59:59.123Z' },
  ])('accepts compatible published, virtual and zero states', (value) => {
    expect(isServiceMeta(value)).toBe(true);
  });

  it.each([
    null, [], {}, { ...fixture, extra: 'private' },
    { ...fixture, base_rate: null }, { ...fixture, base_rate: -0.01 },
    { ...fixture, base_rate: NaN }, { ...fixture, base_rate: Infinity },
    { ...fixture, base_rate_status: 'checked' }, { ...fixture, virtual_data: undefined },
    { ...fixture, virtual_data: 'false' }, { ...fixture, daily_remaining: -1 },
    { ...fixture, daily_remaining: 1.5 }, { ...fixture, daily_remaining: Number.MAX_SAFE_INTEGER + 1 },
    ...['2026-02-29T00:00:00Z', '2026-04-31T00:00:00Z', '2026-11-12T24:00:00Z',
      '2026-11-12T10:00:00', '2026-11-12T10:00:00+24:00', 'not a timestamp']
      .map((data_updated_at) => ({ ...fixture, data_updated_at })),
  ])('rejects invalid and ambiguous payloads', (value) => {
    expect(isServiceMeta(value)).toBe(false);
  });
});

describe('GET /api/v1/meta', () => {
  it('uses only a relative no-store GET and returns the validated body', async () => {
    const fetchFn = vi.fn<typeof fetch>().mockResolvedValue(Response.json(fixture));
    await expect(fetchServiceMeta({ fetchFn })).resolves.toEqual(fixture);
    expect(fetchFn).toHaveBeenCalledWith('/api/v1/meta', { signal: expect.any(AbortSignal), cache: 'no-store' });
  });

  it.each([
    () => Response.json({ message: 'PRIVATE details' }, { status: 503 }),
    () => Response.json({ ...fixture, base_rate: null }),
    () => new Response('PRIVATE invalid JSON'),
  ])('fails safely on HTTP and response errors', async (response) => {
    const fetchFn: typeof fetch = async () => response();
    await expect(fetchServiceMeta({ fetchFn })).rejects.toThrow('자료 상태를 확인하지 못했어요.');
  });

  it('does not expose network errors', async () => {
    const fetchFn: typeof fetch = async () => { throw new Error('PRIVATE network details'); };
    await expect(fetchServiceMeta({ fetchFn })).rejects.toThrow(/^자료 상태를 확인하지 못했어요\.$/);
  });

  it('does not send a request after cancellation', async () => {
    const fetchFn = vi.fn<typeof fetch>();
    const controller = new AbortController();
    controller.abort('PRIVATE cancellation');
    await expect(fetchServiceMeta({ fetchFn, signal: controller.signal })).rejects.toThrow('자료 상태');
    expect(fetchFn).not.toHaveBeenCalled();
  });

  it.each(['timeout', 'cancel'])('stops an in-flight fetch on %s', async (mode) => {
    vi.useFakeTimers();
    const controller = new AbortController();
    let sentSignal: AbortSignal | undefined;
    const fetchFn: typeof fetch = async (_url, options) => new Promise<Response>((_resolve, reject) => {
      sentSignal = options?.signal ?? undefined;
      sentSignal?.addEventListener('abort', () => { reject(new Error('PRIVATE abort')); }, { once: true });
    });
    const pending = fetchServiceMeta({ fetchFn, signal: controller.signal });
    const rejected = expect(pending).rejects.toThrow('자료 상태');
    if (mode === 'timeout') await vi.advanceTimersByTimeAsync(8_000);
    else controller.abort();
    await rejected;
    expect(sentSignal?.aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });
});
