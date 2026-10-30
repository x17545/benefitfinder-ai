// 추천·정책 상세가 공유하는 응답 값과 source/evidence 계약의 런타임 검사.
export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function isString(value: unknown): value is string {
  return typeof value === 'string';
}

export function isNonEmptyString(value: unknown): value is string {
  return isString(value) && value.length > 0;
}

export function isMoney(value: unknown): boolean {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0;
}

export function isNullableMoney(value: unknown): boolean {
  return value === null || isMoney(value);
}

export function isNullableString(value: unknown): boolean {
  return value === null || isString(value);
}

export function optional(value: unknown, check: (item: unknown) => boolean): boolean {
  return value === undefined || check(value);
}

export function arrayOf(value: unknown, check: (item: unknown) => boolean, min = 0, max = Infinity): value is unknown[] {
  return Array.isArray(value) && value.length >= min && value.length <= max && value.every(check);
}

export function isDateTime(value: unknown): boolean {
  return isNonEmptyString(value) && !Number.isNaN(Date.parse(value));
}

export function isHttpUrl(value: unknown): boolean {
  if (!isNonEmptyString(value)) { return false; }
  try {
    const url = new URL(value);
    return url.protocol === 'https:' || url.protocol === 'http:';
  } catch {
    return false;
  }
}

export function isSource(value: unknown): boolean {
  return isRecord(value) && isHttpUrl(value.url) && isDateTime(value.checked_at) &&
    isNonEmptyString(value.document_version);
}

export function isEvidence(value: unknown): boolean {
  return isRecord(value) && isNonEmptyString(value.quote) && isSource(value.source) &&
    optional(value.location, isNonEmptyString);
}
