import type { SelectOptions } from './generated/options.ts';
import { OPTIONS_PATH } from '../config/api.ts';

export type { SelectOptions };

export interface FetchSelectOptionsOptions {
  signal?: AbortSignal;
  /** 테스트에서 바꿔 끼우는 fetch */
  fetchFn?: typeof fetch;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function isSigungu(value: unknown): boolean {
  return isRecord(value) && typeof value.code === 'string' && typeof value.name === 'string';
}

function isRegion(value: unknown): boolean {
  return (
    isRecord(value) &&
    typeof value.sido_code === 'string' &&
    typeof value.sido_name === 'string' &&
    Array.isArray(value.sigungu) &&
    value.sigungu.every(isSigungu)
  );
}

/** 서버 응답이 약속한 모양인지 확인한다. 아니면 쓰지 않는다 */
export function isSelectOptions(value: unknown): value is SelectOptions {
  return (
    isRecord(value) &&
    Array.isArray(value.regions) &&
    value.regions.length > 0 &&
    value.regions.every(isRegion) &&
    isRecord(value.bands)
  );
}

/** GET /api/v1/options. 실패하거나 모양이 다르면 오류를 던진다 */
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
