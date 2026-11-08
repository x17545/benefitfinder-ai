// S5 정책 상세에서 쓰는 값 꺼내기. 판정·금액은 서버 값을 그대로 쓰고, 읽기 좋은 글자만 만든다.
import type { NormalizedBenefit } from '../../shared/api/benefits.ts';
import type { RecommendationResponse } from '../../shared/api/recommendations.ts';
import { formatManWon } from '../benefits/format.ts';
import type { PortfolioPolicy } from '../benefits/resultModel.ts';

export type ConditionResult = PortfolioPolicy['eligibility'][number];

/** 마지막 분석 결과에서 이 정책의 내 자격 진단 */
export interface MyPolicyResult {
  eligibility: ConditionResult[];
  aiReason: string | null;
  /** 결과 설명을 생성형 AI가 썼는지 */
  aiGenerated: boolean;
}

const PERIODS = ['1y', '2y', 'all'] as const;

/** 조합 안에서 이 정책을 찾는다. 기간·현재가치가 달라도 자격 진단은 같으므로 처음 찾은 것을 쓴다 */
export function findMyPolicyResult(result: RecommendationResponse | null, policyId: string): MyPolicyResult | null {
  if (result === null) {
    return null;
  }
  for (const period of PERIODS) {
    for (const view of [result.results[period].pv_off, result.results[period].pv_on]) {
      for (const portfolio of view.portfolios) {
        const policy = portfolio.policies.find((item) => item.policy_id === policyId);
        if (policy !== undefined) {
          const reason = policy.ai_reason ?? null;
          return {
            eligibility: policy.eligibility,
            aiReason: reason === '' ? null : reason,
            aiGenerated: result.ai_generated,
          };
        }
      }
    }
  }
  return null;
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

/** 원문 인용: 정책 상세의 조건 근거를 먼저 쓰고, 없으면 판정 결과의 근거를 쓴다 */
export function conditionQuotes(
  benefit: NormalizedBenefit,
  condition: ConditionResult,
): { quote: string; location: string | null }[] {
  const fromBenefit = benefit.eligibility_conditions.find((item) => item.id === condition.condition_id)?.evidence;
  const evidence = fromBenefit !== undefined && fromBenefit.length > 0 ? fromBenefit : condition.evidence;
  return evidence.map((item) => ({ quote: item.quote, location: item.location ?? null }));
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
  if (amount.max_total_won !== null && amount.max_total_won !== undefined) {
    return `최대 ${formatManWon(amount.max_total_won)}`;
  }
  return '금액 확인 필요';
}
