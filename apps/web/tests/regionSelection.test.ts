import { describe, expect, it } from 'vitest';
import {
  isValidRegionSelection, normalizeRegionAnswers, resolveSidoSelection,
} from '../src/features/profile/regionSelection.ts';
import type { Region } from '../src/features/profile/regionSelection.ts';

const regions: Region[] = [
  { sido_code: '11', sido_name: '가상시', sigungu: [{ code: '11110', name: '가상구' }] },
  { sido_code: '36', sido_name: '가상특별시', sigungu: [{ code: '36110', name: '가상특별시' }] },
  { sido_code: '41', sido_name: '가상도', sigungu: [
    { code: '41110', name: '가상시' }, { code: '41130', name: '다른시' },
  ] },
];

describe('region selection against the current catalog', () => {
  it('clears a previous child on parent change and auto-selects a sole child', () => {
    expect(resolveSidoSelection(regions, '41')).toEqual({ sidoCode: '41', regionCode: null, regionLabel: null });
    expect(resolveSidoSelection(regions, '36')).toEqual({ sidoCode: '36', regionCode: '36110', regionLabel: '가상특별시' });
    expect(resolveSidoSelection(regions, '99')).toBeNull();
  });
  it('rejects stale, incomplete and foreign child codes', () => {
    for (const [sido, child] of [[null, '41110'], ['41', null], ['99', '99999'], ['11', '41110'], ['41', '41199']]) {
      expect(isValidRegionSelection(regions, sido, child)).toBe(false);
    }
    expect(isValidRegionSelection(regions, '41', '41110')).toBe(true);
  });
  it('clears obsolete codes and refreshes names without guessing a child', () => {
    expect(normalizeRegionAnswers(regions, '99', '99999')).toEqual({ sidoCode: null, regionCode: null, regionLabel: null });
    expect(normalizeRegionAnswers(regions, '41', '11110')).toEqual({ sidoCode: '41', regionCode: null, regionLabel: null });
    expect(normalizeRegionAnswers(regions, '41', '41110')).toEqual({ sidoCode: '41', regionCode: '41110', regionLabel: '가상도 가상시' });
  });
});
