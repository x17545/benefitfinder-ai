import type { ServiceMeta } from './generated/meta.ts';

const FIELDS = new Set(['data_updated_at', 'base_rate', 'base_rate_status', 'daily_remaining', 'virtual_data']);
const REQUIRED = ['data_updated_at', 'base_rate', 'base_rate_status', 'daily_remaining'];
const FAILURE = '자료 상태를 확인하지 못했어요.';

function isTimestamp(value: unknown): value is string {
  if (typeof value !== 'string') return false;
  const match = /^(\d{4})-(\d{2})-(\d{2})[Tt](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:[Zz]|([+-])(\d{2}):(\d{2}))$/.exec(value);
  if (match === null) return false;
  const [, y, m, d, h, min, s, , oh, om] = match;
  const year = Number(y), month = Number(m), day = Number(d);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return month >= 1 && month <= 12 && day >= 1 && day <= days[month - 1] &&
    Number(h) < 24 && Number(min) < 60 && Number(s) < 60 &&
    (oh === undefined || (Number(oh) < 24 && Number(om) < 60)) && Number.isFinite(Date.parse(value));
}

/** 공개 계약의 값과 필드만 허용한다. unknown 금리를 0 또는 검증 완료로 바꾸지 않는다. */
export function isServiceMeta(value: unknown): value is ServiceMeta {
  if (typeof value !== 'object' || value === null) return false;
  if (Object.keys(value).some((key) => !FIELDS.has(key)) ||
    REQUIRED.some((key) => !Object.hasOwn(value, key))) return false;
  const item = value as Record<string, unknown>;
  if (!isTimestamp(item.data_updated_at)) return false;
  if (item.base_rate !== null &&
    (typeof item.base_rate !== 'number' || !Number.isFinite(item.base_rate) || item.base_rate < 0)) return false;
  if (item.base_rate_status !== 'verified' && item.base_rate_status !== 'unknown') return false;
  if (item.base_rate_status === 'verified' && item.base_rate === null) return false;
  if (typeof item.daily_remaining !== 'number' || !Number.isSafeInteger(item.daily_remaining) || item.daily_remaining < 0) return false;
  return !Object.hasOwn(item, 'virtual_data') || typeof item.virtual_data === 'boolean';
}

export interface FetchServiceMetaOptions {
  signal?: AbortSignal;
  fetchFn?: typeof fetch;
}

/** same-origin GET만 사용하며, 취소·8초 제한·실패는 원문이나 내부 오류를 노출하지 않는다. */
export async function fetchServiceMeta({ signal, fetchFn = fetch }: FetchServiceMetaOptions = {}): Promise<ServiceMeta> {
  const controller = new AbortController();
  const abort = (): void => { controller.abort(); };
  signal?.addEventListener('abort', abort, { once: true });
  if (signal?.aborted === true) abort();
  const timer = setTimeout(abort, 8_000);
  try {
    controller.signal.throwIfAborted();
    const response = await fetchFn('/api/v1/meta', { signal: controller.signal, cache: 'no-store' });
    controller.signal.throwIfAborted();
    if (!response.ok) throw new Error(FAILURE);
    const body: unknown = await response.json();
    controller.signal.throwIfAborted();
    if (!isServiceMeta(body)) throw new Error(FAILURE);
    return body;
  } catch {
    throw new Error(FAILURE);
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', abort);
  }
}
