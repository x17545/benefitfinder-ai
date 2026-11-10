import { describe, expect, it } from 'vitest';
import { fetchSelectOptions, isSelectOptions } from '../src/shared/api/options.ts';
import optionsText from '../../../contracts/fixtures/options.json?raw';

const VALID: unknown = JSON.parse(optionsText);

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

  it('accepts an empty catalog for a distinct empty state without fixing province count', () => {
    if (!isSelectOptions(VALID)) { throw new Error('invalid fixture'); }
    expect(isSelectOptions({ ...VALID, regions: [] })).toBe(true);
    expect(isSelectOptions({ ...VALID, regions: [VALID.regions[0]] })).toBe(true);
  });

  it('rejects empty bands, malformed codes, duplicate codes and cross-province children', () => {
    if (!isSelectOptions(VALID)) { throw new Error('invalid fixture'); }
    const region = VALID.regions[0];
    for (const invalid of [
      { ...VALID, bands: {} },
      { ...VALID, bands: { ...VALID.bands, income: [] } },
      { ...VALID, regions: [region, region] },
      { ...VALID, regions: [{ ...region, sido_code: '1' }] },
      { ...VALID, regions: [{ ...region, sido_name: ' ' }] },
      { ...VALID, regions: [{ ...region, sigungu: [] }] },
      { ...VALID, regions: [{ ...region, sigungu: [region.sigungu[0], region.sigungu[0]] }] },
      { ...VALID, regions: [{ ...region, sigungu: [{ code: '41110', name: '가상 다른 시도' }] }] },
      { ...VALID, regions: [{ ...region, sigungu: [{ code: '1111', name: '가상 구' }] }] },
    ]) { expect(isSelectOptions(invalid)).toBe(false); }
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
