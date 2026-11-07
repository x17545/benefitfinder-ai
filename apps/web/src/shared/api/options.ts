import type { SelectOptions } from './generated/options.ts';
import { OPTIONS_PATH } from '../config/api.ts';

export type { SelectOptions };

export interface FetchSelectOptionsOptions {
  signal?: AbortSignal;
  /** test-injected fetch */
  fetchFn?: typeof fetch;
}

const SIDO_CODE_PATTERN = /^[0-9]{2}$/;
const SIGUNGU_CODE_PATTERN = /^[0-9]{5}$/;
const BAND_KEYS = ['income', 'asset', 'deposit', 'rent', 'parent_income', 'parent_asset'] as const;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0;
}

function isBandItem(value: unknown): boolean {
  return isRecord(value) && isNonEmptyString(value.value) && isNonEmptyString(value.label);
}

function isBandList(value: unknown): boolean {
  return Array.isArray(value) && value.length > 0 && value.every(isBandItem);
}

function isBands(value: unknown): boolean {
  if (!isRecord(value)) {
    return false;
  }
  return BAND_KEYS.every((key) => isBandList(value[key]));
}

function isSigungu(value: unknown, sidoCode: string): boolean {
  if (!isRecord(value)) {
    return false;
  }
  if (typeof value.code !== 'string' || !SIGUNGU_CODE_PATTERN.test(value.code)) {
    return false;
  }
  if (!isNonEmptyString(value.name)) {
    return false;
  }
  return value.code.startsWith(sidoCode);
}

function isRegion(value: unknown): boolean {
  if (!isRecord(value)) {
    return false;
  }
  if (typeof value.sido_code !== 'string' || !SIDO_CODE_PATTERN.test(value.sido_code)) {
    return false;
  }
  if (!isNonEmptyString(value.sido_name)) {
    return false;
  }
  if (!Array.isArray(value.sigungu) || value.sigungu.length === 0) {
    return false;
  }
  const codes = new Set<string>();
  for (const item of value.sigungu) {
    if (!isSigungu(item, value.sido_code)) {
      return false;
    }
    const code = (item as Record<string, unknown>).code as string;
    if (codes.has(code)) {
      return false;
    }
    codes.add(code);
  }
  return true;
}

/** Validate server payload shape. Empty region list is valid (empty UI); province count is not fixed. */
export function isSelectOptions(value: unknown): value is SelectOptions {
  if (!isRecord(value)) {
    return false;
  }
  if (!Array.isArray(value.regions)) {
    return false;
  }
  if (!value.regions.every(isRegion)) {
    return false;
  }
  const sidoCodes = new Set<string>();
  for (const region of value.regions) {
    const code = (region as Record<string, unknown>).sido_code as string;
    if (sidoCodes.has(code)) {
      return false;
    }
    sidoCodes.add(code);
  }
  return isBands(value.bands);
}

/** GET /api/v1/options. Throw on HTTP or shape failure. */
export async function fetchSelectOptions(
  options: FetchSelectOptionsOptions = {},
): Promise<SelectOptions> {
  const { signal, fetchFn = fetch } = options;
  const response = await fetchFn(OPTIONS_PATH, { signal });
  if (!response.ok) {
    throw new Error(`options request failed: HTTP ${response.status.toString()}`);
  }
  const body: unknown = await response.json();
  if (!isSelectOptions(body)) {
    throw new Error('options request failed: unexpected response shape');
  }
  return body;
}
