import type { SelectOptions } from '../../shared/api/options.ts';

export type Region = SelectOptions['regions'][number];

export interface ResolvedRegion {
  sidoCode: string | null;
  regionCode: string | null;
  regionLabel: string | null;
}

const SIDO_CODE_PATTERN = /^[0-9]{2}$/;
const SIGUNGU_CODE_PATTERN = /^[0-9]{5}$/;

/** Display name: Sejong-style same-name pair collapses to one name. */
export function regionLabel(sidoName: string, sigunguName: string): string {
  return sidoName === sigunguName ? sigunguName : `${sidoName} ${sigunguName}`;
}

export function findSido(regions: Region[], sidoCode: string | null): Region | undefined {
  if (sidoCode === null) {
    return undefined;
  }
  return regions.find((region) => region.sido_code === sidoCode);
}

/** Format + prefix + catalog membership. No province-count check. */
export function isValidRegionSelection(
  regions: Region[],
  sidoCode: string | null,
  regionCode: string | null,
): boolean {
  if (sidoCode === null || regionCode === null) {
    return false;
  }
  if (!SIDO_CODE_PATTERN.test(sidoCode) || !SIGUNGU_CODE_PATTERN.test(regionCode)) {
    return false;
  }
  if (!regionCode.startsWith(sidoCode)) {
    return false;
  }
  const sido = findSido(regions, sidoCode);
  if (sido === undefined) {
    return false;
  }
  return sido.sigungu.some((item) => item.code === regionCode);
}

/** Parent change result: sole-child sido auto-selects, otherwise clears child. */
export function resolveSidoSelection(regions: Region[], sidoCode: string): ResolvedRegion | null {
  const sido = findSido(regions, sidoCode);
  if (sido === undefined) {
    return null;
  }
  if (sido.sigungu.length === 1) {
    const only = sido.sigungu[0];
    return { sidoCode, regionCode: only.code, regionLabel: regionLabel(sido.sido_name, only.name) };
  }
  return { sidoCode, regionCode: null, regionLabel: null };
}

/** Reconcile stored codes against the fresh catalog: drop stale, fix label. */
export function normalizeRegionAnswers(
  regions: Region[],
  sidoCode: string | null,
  regionCode: string | null,
): ResolvedRegion {
  const sido = sidoCode === null ? undefined : findSido(regions, sidoCode);
  if (sido === undefined || sidoCode === null) {
    return { sidoCode: null, regionCode: null, regionLabel: null };
  }
  const sigungu = regionCode === null ? undefined : sido.sigungu.find((item) => item.code === regionCode);
  if (sigungu === undefined) {
    return { sidoCode, regionCode: null, regionLabel: null };
  }
  const label = regionLabel(sido.sido_name, sigungu.name);
  return { sidoCode, regionCode: sigungu.code, regionLabel: label };
}
