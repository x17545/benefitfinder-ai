import type { NormalizedBenefit } from './generated/benefit.ts';
import { BENEFITS_PATH } from '../config/api.ts';
import {
  arrayOf, isEvidence, isHttpUrl, isMoney, isNonEmptyString, isNullableMoney,
  isNullableString, isRecord, isSource, optional,
} from './responseValidation.ts';

export type { NormalizedBenefit };

function isNullableDate(value: unknown): boolean {
  if (value === null) { return true; }
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) { return false; }
  const date = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

function isApplication(value: unknown): boolean {
  return isRecord(value) &&
    (value.status === 'open' || value.status === 'closed' || value.status === 'upcoming' || value.status === 'unknown') &&
    isNullableDate(value.starts_on) && isNullableDate(value.ends_on) &&
    (value.url === null || isHttpUrl(value.url));
}

function isCondition(value: unknown): boolean {
  return isRecord(value) && isNonEmptyString(value.id) && isNonEmptyString(value.description) &&
    arrayOf(value.evidence, isEvidence, 1);
}

function isBenefitAmount(value: unknown): boolean {
  return value === null || (isRecord(value) &&
    optional(value.type, (item) => item === null || item === 'monthly' || item === 'lump_sum' || item === 'maturity' || item === 'interest') &&
    optional(value.amount_won, isNullableMoney) && optional(value.max_total_won, isNullableMoney) &&
    optional(value.period_months, (item) => item === null || (isMoney(item) && typeof item === 'number' && item >= 1)) &&
    optional(value.pay_timing, (item) => item === null || item === 'monthly_from_start' || item === 'at_maturity' || item === 'yearly' || item === 'once') &&
    optional(value.amount_text, isNullableString) &&
    optional(value.amount_status, (item) => item === 'verified' || item === 'unknown'));
}

function isConflict(value: unknown): boolean {
  return isRecord(value) && isNonEmptyString(value.policy_id) && arrayOf(value.evidence, isEvidence, 1);
}

function isNullableBoolean(value: unknown): boolean {
  return value === null || typeof value === 'boolean';
}

/** 정책 상세의 필수 값과 화면이 읽는 금액·모집·중복 제한의 중첩 구조를 확인한다. */
export function isNormalizedBenefit(value: unknown): value is NormalizedBenefit {
  return (
    isRecord(value) &&
    isNonEmptyString(value.id) && isNonEmptyString(value.domain) && /^[a-z][a-z0-9_-]*$/.test(value.domain) &&
    isNonEmptyString(value.title) && isNonEmptyString(value.summary) && isNonEmptyString(value.provider) &&
    arrayOf(value.categories, isNonEmptyString) && new Set(value.categories).size === value.categories.length &&
    isApplication(value.application) && arrayOf(value.eligibility_conditions, isCondition) && isSource(value.source) &&
    isRecord(value.domain_data) && optional(value.benefit_amount, isBenefitAmount) &&
    optional(value.reviewed, isNullableBoolean) && optional(value.lifetime_once, isNullableBoolean) &&
    optional(value.conflicts, (item) => item === null || arrayOf(item, isConflict))
  );
}

export interface FetchBenefitOptions {
  signal?: AbortSignal;
  /** 테스트에서 바꿔 끼우는 fetch */
  fetchFn?: typeof fetch;
}

/** GET /api/v1/benefits/{benefit_id} */
export async function fetchBenefit(id: string, options: FetchBenefitOptions = {}): Promise<NormalizedBenefit> {
  const { signal, fetchFn = fetch } = options;
  const response = await fetchFn(`${BENEFITS_PATH}/${encodeURIComponent(id)}`, { signal });
  if (!response.ok) {
    throw new Error(`benefit request failed: HTTP ${response.status.toString()}`);
  }
  const body: unknown = await response.json();
  if (!isNormalizedBenefit(body)) {
    throw new Error('benefit request failed: unexpected response shape');
  }
  return body;
}
