import { describe, expect, it } from 'vitest';
import handler from '../api/backend-unavailable.ts';
import { fetchHealth } from '../src/shared/api/health.ts';
import { fetchSelectOptions } from '../src/shared/api/options.ts';

describe('preview without a backend', () => {
  it.each(['GET', 'POST'])('returns a non-cacheable API error for %s without reading input', async (method) => {
    const request = new Request('https://preview.example/api/v1/recommendations', {
      method,
      ...(method === 'POST' ? { body: '{"fictional_private_marker":"do-not-echo"}' } : {}),
    });
    const serve: (request: Request) => Response = handler.fetch;
    const response = serve(request);
    expect(response.status).toBe(503);
    expect(response.headers.get('cache-control')).toBe('no-store');
    expect(response.headers.get('content-type')).toContain('application/json');
    const error: unknown = await response.json();
    expect(error).toEqual({
      request_id: expect.stringMatching(/^[0-9a-f-]{36}$/),
      code: 'dependency_unavailable',
      message: expect.any(String),
    });
    expect(JSON.stringify(error)).not.toContain('do-not-echo');
    expect(request.bodyUsed).toBe(false);
  });

  it('fails health and region loading instead of reporting an available backend', async () => {
    const fetchFn: typeof fetch = async () => handler.fetch();
    await expect(fetchHealth({ fetchFn, retries: 0 })).rejects.toThrow('HTTP 503');
    await expect(fetchSelectOptions({ fetchFn })).rejects.toThrow('HTTP 503');
  });
});
