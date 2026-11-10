import type { RecommendationResponse } from './generated/recommendation-response.ts';
import {
  arrayOf, isDateTime, isEvidence, isMoney, isNonEmptyString, isNullableMoney,
  isNullableString, isRecord, isSource, isString, optional,
} from './responseValidation.ts';

function isEligibilityStatus(value: unknown): boolean {
  return value === 'met' || value === 'not_met' || value === 'needs_review';
}

function isCondition(value: unknown): boolean {
  return isRecord(value) && isNonEmptyString(value.condition_id) && isEligibilityStatus(value.status) &&
    isNonEmptyString(value.reason) && arrayOf(value.evidence, isEvidence, value.status === 'needs_review' ? 0 : 1);
}

function isPolicy(value: unknown): boolean {
  if (!isRecord(value) || !isNonEmptyString(value.policy_id) || !arrayOf(value.eligibility, isCondition) ||
    !isSource(value.source) || !optional(value.amount_in_period_won, isNullableMoney) ||
    !optional(value.original_amount_won, isNullableMoney) ||
    !optional(value.amount_status, (item) => item === 'verified' || item === 'unknown')) {
    return false;
  }
  return isPolicyAiFields(value);
}

/** ai_sources 항목은 evidence에 location(조항 ID) 필수. 다른 evidence의 location 선택성은 유지한다. */
function isAiSource(value: unknown): boolean {
  return isRecord(value) && isEvidence(value) && isNonEmptyString(value.location);
}

/** true면 설명과 근거가 필수다. 정책별 플래그 생략은 기존 응답과 호환한다. */
function isPolicyAiFields(value: Record<string, unknown>): boolean {
  const flag = value.ai_generated;
  const reason = value.ai_reason;
  const sources = value.ai_sources;
  if (flag === true) {
    return isNonEmptyString(reason) && arrayOf(sources, isAiSource, 1, 5);
  }
  if (flag === false) {
    return optional(reason, isNullableString) &&
      (sources === undefined || (Array.isArray(sources) && sources.length === 0));
  }
  if (flag === undefined) {
    return optional(reason, isNullableString) &&
      optional(sources, (item) => arrayOf(item, isAiSource, 0, 5));
  }
  return false;
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

/** F12 preview 일관성: ID 동일·확정 금액 없음·자격 미확정만 허용한다. */
function isReviewExplanationConsistent(outer: Record<string, unknown>, inner: unknown): boolean {
  if (!isPolicy(inner)) {
    return false;
  }
  const detail = inner as Record<string, unknown>;
  if (detail.policy_id !== outer.policy_id) {
    return false;
  }
  const amount = detail.amount_in_period_won;
  const original = detail.original_amount_won;
  if (typeof amount === 'number' || typeof original === 'number') {
    return false;
  }
  if (detail.amount_status === 'verified') {
    return false;
  }
  const eligibility = detail.eligibility;
  if (!Array.isArray(eligibility)) {
    return false;
  }
  for (const condition of eligibility) {
    if (!isRecord(condition) || condition.status !== 'needs_review') {
      return false;
    }
  }
  if (outer.eligibility_summary !== 'needs_review') {
    return false;
  }
  return true;
}

function isNotReviewed(value: unknown): boolean {
  if (!isRecord(value) || !isNonEmptyString(value.policy_id) || !isEligibilityStatus(value.eligibility_summary)) {
    return false;
  }
  if (!optional(value.policy_name, isNonEmptyString)) {
    return false;
  }
  if (!optional(value.review_scope, isNonEmptyString)) {
    return false;
  }
  if (!optional(value.verification_only, (item) => item === true)) {
    return false;
  }
  if (value.review_explanation === undefined) {
    return true;
  }
  if (!isNonEmptyString(value.policy_name) || !isNonEmptyString(value.review_scope) || value.verification_only !== true) {
    return false;
  }
  return isReviewExplanationConsistent(value, value.review_explanation);
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
  if (!Object.keys(value.results).every((key) => key === '1y' || key === '2y' || key === 'all')) {
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
