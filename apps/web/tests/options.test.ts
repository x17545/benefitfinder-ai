import { describe, expect, it } from 'vitest';
import { fetchSelectOptions, isSelectOptions } from '../src/shared/api/options.ts';

const VALID = {
  regions: [
    { sido_code: '11', sido_name: '[가상] 서울', sigungu: [{ code: '11110', name: '[가상] 종로구' }] },
  ],
  bands: {},
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('isSelectOptions', () => {
  it('accepts the contract shape', () => {
    expect(isSelectOptions(VALID)).toBe(true);
  });

  it('rejects missing or broken region lists', () => {
    expect(isSelectOptions(null)).toBe(false);
    expect(isSelectOptions({ regions: [], bands: {} })).toBe(false);
    expect(isSelectOptions({ regions: [{ sido_code: 11 }], bands: {} })).toBe(false);
    expect(
      isSelectOptions({
        regions: [{ sido_code: '11', sido_name: '서울', sigungu: [{ code: 11110 }] }],
        bands: {},
      }),
    ).toBe(false);
  });
});

describe('fetchSelectOptions', () => {
  it('returns a valid payload', async () => {
    const fetchFn: typeof fetch = async () => jsonResponse(VALID);
    await expect(fetchSelectOptions({ fetchFn })).resolves.toEqual(VALID);
  });

  it('fails on non-2xx status', async () => {
    const fetchFn: typeof fetch = async () => jsonResponse(VALID, 404);
    await expect(fetchSelectOptions({ fetchFn })).rejects.toThrow(/HTTP 404/);
  });

  it('fails on an unexpected shape', async () => {
    const fetchFn: typeof fetch = async () => jsonResponse({ regions: 'none' });
    await expect(fetchSelectOptions({ fetchFn })).rejects.toThrow(/unexpected/);
  });
});
