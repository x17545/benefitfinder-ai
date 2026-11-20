// S5 정책 상세에서 쓰는 값 꺼내기. 판정·금액은 서버 값을 그대로 쓰고, 읽기 좋은 글자만 만든다.
import type { NormalizedBenefit } from '../../shared/api/benefits.ts';
import type { RecommendationResponse } from '../../shared/api/recommendations.ts';
import { formatManWon } from '../benefits/format.ts';
import type { ResultBasis } from '../benefits/resultBasis.ts';
import { pickView } from '../benefits/resultModel.ts';
import type { PortfolioPolicy } from '../benefits/resultModel.ts';

export type ConditionResult = PortfolioPolicy['eligibility'][number];
/**
 * 공식 원문의 보존용 구간과 금액 계산 근거를 일반 자격 조건에서 분리한다.
 * 서버 판정 결과와 원문 근거는 변경하지 않는다.
 */
export function partitionPolicyEvidence(
  conditions: readonly ConditionResult[],
): {
  eligibility: ConditionResult[];
  officialTerms: ConditionResult[];
  calculationEvidence: ConditionResult[];
} {
  const eligibility: ConditionResult[] = [];
  const officialTerms: ConditionResult[] = [];
  const calculationEvidence: ConditionResult[] = [];

  for (const condition of conditions) {
    if (/^official_source_terms_[0-9]+$/.test(condition.condition_id)) {
      officialTerms.push(condition);
    } else if (condition.condition_id === 'calculated_component') {
      calculationEvidence.push(condition);
    } else {
      eligibility.push(condition);
    }
  }

  return { eligibility, officialTerms, calculationEvidence };
}

/** 같은 선택 기준·문서 버전·자료 종류의 분석만 상세에 연결한다. */
export function findMyPolicyResult(
  result: RecommendationResponse, benefit: NormalizedBenefit, basis: ResultBasis,
  isSample: boolean, sampleAnalysis: boolean,
): PortfolioPolicy | null {
  if ((isSample || benefit.virtual_data === true) !== (sampleAnalysis || result.virtual_data === true)) return null;
  const view = pickView(result, basis.period, basis.present_value);
  const inPortfolio = view.portfolios.flatMap((portfolio) => portfolio.policies).find((policy) =>
    policy.policy_id === benefit.id && policy.source.url === benefit.source.url &&
    policy.source.document_version === benefit.source.document_version);
  if (inPortfolio !== undefined) return inPortfolio;

  const fallback = (view.fallback_list ?? []).find((item) =>
    item.policy_id === benefit.id && item.source?.url === benefit.source.url &&
    item.source?.document_version === benefit.source.document_version);

  if (fallback?.source === undefined || fallback.eligibility === undefined) return null;
  return { ...fallback, source: fallback.source, eligibility: fallback.eligibility };
}

/** 문구 S5-SUM: "조건 3개 중 2개 충족, 1개 확인 필요" */
export function diagnosisSummary(eligibility: readonly ConditionResult[]): string {
  const met = eligibility.filter((item) => item.status === 'met').length;
  const review = eligibility.filter((item) => item.status === 'needs_review').length;
  const notMet = eligibility.filter((item) => item.status === 'not_met').length;
  const parts = [`${String(met)}개 충족`];
  if (review > 0) {
    parts.push(`${String(review)}개 확인 필요`);
  }
  if (notMet > 0) {
    parts.push(`${String(notMet)}개 미충족`);
  }
  return `조건 ${String(eligibility.length)}개 중 ${parts.join(', ')}`;
}

/** "2026-10-01" → "2026.10.01" */
function dotDate(value: string): string {
  return value.replaceAll('-', '.');
}

/** 신청 기간. 날짜가 없으면 추정하지 않는다 */
export function applyPeriod(application: NormalizedBenefit['application']): string {
  const { starts_on: starts, ends_on: ends } = application;
  if (starts !== null && ends !== null) {
    return `${dotDate(starts)} ~ ${dotDate(ends)}`;
  }
  if (starts !== null) {
    return `${dotDate(starts)}부터`;
  }
  if (ends !== null) {
    return `${dotDate(ends)}까지`;
  }
  return '공식 페이지에서 확인해 주세요';
}

/** 지원 내용 한 줄. 원문 요약(amount_text)이 있으면 그대로, 없으면 최대 금액 */
export function amountSummary(amount: NormalizedBenefit['benefit_amount']): string {
  if (amount === null || amount === undefined) {
    return '금액 정보가 없어요. 공식 페이지에서 확인해 주세요';
  }
  if (amount.amount_text !== null && amount.amount_text !== undefined && amount.amount_text !== '') {
    return amount.amount_text;
  }
  if (amount.amount_status !== 'unknown' && amount.max_total_won !== null && amount.max_total_won !== undefined) {
    return `최대 ${formatManWon(amount.max_total_won)}`;
  }
  return '금액 확인 필요';
}

/** 원문이 만기 일시 지급으로 검수된 경우에만 표시한다. 금액의 현금·적립 구성을 추정하지 않는다. */
export function payoutTimingSummary(amount: NormalizedBenefit['benefit_amount']): string | null {
  if (amount === null || amount === undefined) {
    return null;
  }
  return amount.type === 'maturity' || amount.pay_timing === 'at_maturity' ? '만기 시 지급' : null;
}

/** S5에서 조합 진단이 없을 때, 이 정책이 결과의 어느 목록에 있었는지 */
export type ResultPolicyStatus =
  | { kind: 'fallback' }
  | { kind: 'notEligible'; failed: readonly string[] }
  | { kind: 'closed' }
  | { kind: 'notReviewed'; summary: ConditionResult['status'] }
  | null;

/**
 * 조합(portfolios) 밖의 목록에서 정책을 찾는다. 판정은 서버 값을 그대로 쓰고 새로 계산하지 않는다.
 * 결과에 없으면 null (지난 분석이거나 다른 조건으로 찾은 정책)
 */
export function policyStatusInResult(
  result: RecommendationResponse, basis: ResultBasis, policyId: string,
): ResultPolicyStatus {
  const view = pickView(result, basis.period, basis.present_value);
  if ((view.fallback_list ?? []).some((item) => item.policy_id === policyId)) {
    return { kind: 'fallback' };
  }
  const notEligible = (view.not_eligible ?? []).find((item) => item.policy_id === policyId);
  if (notEligible !== undefined) {
    return { kind: 'notEligible', failed: notEligible.failed };
  }
  if ((view.closed_now ?? []).some((item) => item.policy_id === policyId)) {
    return { kind: 'closed' };
  }
  const notReviewed = (view.not_yet_reviewed ?? []).find((item) => item.policy_id === policyId);
  if (notReviewed !== undefined) {
    return { kind: 'notReviewed', summary: notReviewed.eligibility_summary };
  }
  return null;
}

/** 원문 인용을 접어 둘 기준 글자 수. 이보다 길면 앞부분만 보이고 [원문 전체 보기]로 펼친다 */
export const QUOTE_PREVIEW_LIMIT = 140;

/**
 * 긴 원문 인용의 앞부분. 원문은 바꾸지 않고 화면에서만 줄인다.
 * 기준보다 짧으면 null(그대로 보여 준다). 가능하면 띄어쓰기에서 끊는다.
 */
export function quotePreview(quote: string, limit = QUOTE_PREVIEW_LIMIT): string | null {
  const text = quote.trim();
  if (text.length <= limit) {
    return null;
  }
  const cut = text.slice(0, limit - 20);
  const space = cut.lastIndexOf(' ');
  return `${(space > cut.length * 0.6 ? cut.slice(0, space) : cut).trimEnd()}…`;
}
