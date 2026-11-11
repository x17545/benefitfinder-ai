// S5 정책 상세에서 쓰는 값 꺼내기. 판정·금액은 서버 값을 그대로 쓰고, 읽기 좋은 글자만 만든다.
import type { NormalizedBenefit } from '../../shared/api/benefits.ts';
import type { RecommendationResponse } from '../../shared/api/recommendations.ts';
import { formatManWon } from '../benefits/format.ts';
import type { ResultBasis } from '../benefits/resultBasis.ts';
import { pickView } from '../benefits/resultModel.ts';
import type { PortfolioPolicy } from '../benefits/resultModel.ts';

export type ConditionResult = PortfolioPolicy['eligibility'][number];

/** 같은 선택 기준·문서 버전·자료 종류의 분석만 상세에 연결한다. */
export function findMyPolicyResult(
  result: RecommendationResponse, benefit: NormalizedBenefit, basis: ResultBasis,
  isSample: boolean, sampleAnalysis: boolean,
): PortfolioPolicy | null {
  if ((isSample || benefit.virtual_data === true) !== (sampleAnalysis || result.virtual_data === true)) return null;
  const view = pickView(result, basis.period, basis.present_value);
  return view.portfolios.flatMap((portfolio) => portfolio.policies).find((policy) =>
    policy.policy_id === benefit.id && policy.source.url === benefit.source.url &&
    policy.source.document_version === benefit.source.document_version) ?? null;
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
