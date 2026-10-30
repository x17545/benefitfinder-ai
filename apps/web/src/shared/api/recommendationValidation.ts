import type { RecommendationResponse } from './generated/recommendation-response.ts';

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isString(value: unknown): value is string {
  return typeof value === 'string';
}

function isNonEmptyString(value: unknown): value is string {
  return isString(value) && value.length > 0;
}

function isMoney(value: unknown): boolean {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0;
}

function isNullableMoney(value: unknown): boolean {
  return value === null || isMoney(value);
}

function isNullableString(value: unknown): boolean {
  return value === null || isString(value);
}

function optional(value: unknown, check: (item: unknown) => boolean): boolean {
  return value === undefined || check(value);
}

function arrayOf(value: unknown, check: (item: unknown) => boolean, min = 0, max = Infinity): value is unknown[] {
  return Array.isArray(value) && value.length >= min && value.length <= max && value.every(check);
}

function isDateTime(value: unknown): boolean {
  return isNonEmptyString(value) && !Number.isNaN(Date.parse(value));
}

function isSource(value: unknown): boolean {
  if (!isRecord(value) || !isNonEmptyString(value.url)) {
    return false;
  }
  try {
    const url = new URL(value.url);
    return (url.protocol === 'https:' || url.protocol === 'http:') &&
      isDateTime(value.checked_at) && isNonEmptyString(value.document_version);
  } catch {
    return false;
  }
}

function isEvidence(value: unknown): boolean {
  return isRecord(value) && isNonEmptyString(value.quote) && isSource(value.source) &&
    optional(value.location, isNonEmptyString);
}

function isEligibilityStatus(value: unknown): boolean {
  return value === 'met' || value === 'not_met' || value === 'needs_review';
}

function isCondition(value: unknown): boolean {
  return isRecord(value) && isNonEmptyString(value.condition_id) && isEligibilityStatus(value.status) &&
    isNonEmptyString(value.reason) && arrayOf(value.evidence, isEvidence, value.status === 'needs_review' ? 0 : 1);
}

function isPolicy(value: unknown): boolean {
  return isRecord(value) && isNonEmptyString(value.policy_id) && arrayOf(value.eligibility, isCondition) &&
    isSource(value.source) && optional(value.amount_in_period_won, isNullableMoney) &&
    optional(value.original_amount_won, isNullableMoney) && optional(value.ai_reason, isNullableString) &&
    optional(value.amount_status, (item) => item === 'verified' || item === 'unknown');
}

function isGivenUp(value: unknown): boolean {
  return isRecord(value) && isNonEmptyString(value.policy_id) && optional(value.amount_won, isNullableMoney);
}

function isUnverifiedPair(value: unknown): boolean {
  return isRecord(value) && isNonEmptyString(value.a) && isNonEmptyString(value.b) && isNonEmptyString(value.note);
}

function isPortfolio(value: unknown): boolean {
  return isRecord(value) && typeof value.rank === 'number' && Number.isInteger(value.rank) &&
    value.rank >= 1 && value.rank <= 3 && isMoney(value.total_won) && arrayOf(value.policies, isPolicy, 1) &&
    optional(value.present_value_applied, (item) => typeof item === 'boolean') &&
    optional(value.given_up, (item) => arrayOf(item, isGivenUp)) &&
    optional(value.unverified_pairs, (item) => arrayOf(item, isUnverifiedPair));
}

function isWhy(value: unknown): boolean {
  return value === null || (isRecord(value) &&
    optional(value.gap_won, (item) => item === null || (typeof item === 'number' && Number.isInteger(item))) &&
    optional(value.second_total_won, isNullableMoney) &&
    optional(value.deciding_conflict, (item) => arrayOf(item, isNonEmptyString)));
}

function isExcluded(value: unknown): boolean {
  return isRecord(value) && isNonEmptyString(value.policy_id) && isNonEmptyString(value.reason) &&
    arrayOf(value.evidence, isEvidence, 1);
}

function isNotEligible(value: unknown): boolean {
  return isRecord(value) && isNonEmptyString(value.policy_id) && arrayOf(value.failed, isNonEmptyString, 1);
}

function isNotReviewed(value: unknown): boolean {
  return isRecord(value) && isNonEmptyString(value.policy_id) && isEligibilityStatus(value.eligibility_summary);
}

function isClosed(value: unknown): boolean {
  return isRecord(value) && isNonEmptyString(value.policy_id) && optional(value.last_period, isNullableString) &&
    optional(value.next_expected, isNullableString);
}

function isFallback(value: unknown): boolean {
  return isRecord(value) && isNonEmptyString(value.policy_id) &&
    optional(value.amount_in_period_won, isNullableMoney) && optional(value.reason, isNonEmptyString);
}

function isResultView(value: unknown): boolean {
  if (!isRecord(value) || !arrayOf(value.portfolios, isPortfolio, 0, 3)) {
    return false;
  }
  const calculationValid = value.calculation_status === 'available'
    ? value.unavailable_reason === null
    : value.calculation_status === 'unavailable' && value.portfolios.length === 0 &&
      (value.unavailable_reason === 'missing_discount_rate' || value.unavailable_reason === 'unknown_payment_timing');
  return calculationValid && optional(value.why, isWhy) &&
    optional(value.excluded, (item) => arrayOf(item, isExcluded)) &&
    optional(value.not_eligible, (item) => arrayOf(item, isNotEligible)) &&
    optional(value.not_yet_reviewed, (item) => arrayOf(item, isNotReviewed)) &&
    optional(value.closed_now, (item) => arrayOf(item, isClosed)) &&
    optional(value.fallback_list, (item) => item === null || arrayOf(item, isFallback, 0, 5));
}

function isPeriodResult(value: unknown): boolean {
  return isRecord(value) && isResultView(value.pv_off) && isResultView(value.pv_on);
}

/** API와 캐시의 값은 unknown이다. 화면이 읽는 6개 분기와 중첩 필드를 검증한다. */
export function isRecommendationResponse(value: unknown): value is RecommendationResponse {
  if (!isRecord(value) || !isRecord(value.results)) {
    return false;
  }
  return isNonEmptyString(value.request_id) && isString(value.domain) && /^[a-z][a-z0-9_-]*$/.test(value.domain) &&
    (value.view === 'portfolio' || value.view === 'fallback_list' || value.view === 'empty') &&
    typeof value.ai_generated === 'boolean' && isDateTime(value.data_updated_at) &&
    (value.discount_rate === null || (typeof value.discount_rate === 'number' &&
      Number.isFinite(value.discount_rate) && value.discount_rate >= 0)) &&
    (value.discount_rate_status === 'verified' || value.discount_rate_status === 'unknown') &&
    isPeriodResult(value.results['1y']) && isPeriodResult(value.results['2y']) && isPeriodResult(value.results.all);
}
