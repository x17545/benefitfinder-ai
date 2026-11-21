// 서버 결과에서 화면에 필요한 값만 꺼낸다. 순위·금액은 서버가 계산한 값을 그대로 쓴다.
import type { PolicySummary } from '../../shared/api/generated/recommendation-response.ts';
import type { RecommendationResponse } from '../../shared/api/recommendations.ts';
import { shortPolicyId } from './format.ts';
import type { Period } from './format.ts';

export type ResultView = RecommendationResponse['results']['1y']['pv_off'];
export type Portfolio = ResultView['portfolios'][number];
export type PortfolioPolicy = Portfolio['policies'][number];
export type PolicySummaries = ReadonlyMap<string, PolicySummary>;
export type EligibilityDisplayStatus = 'met' | 'needs_review' | 'not_met' | 'unknown';

type EligibilityStatusItem = {
  status: 'met' | 'needs_review' | 'not_met';
};

/** 정책의 입력 조건 판정 상태. 진단 누락은 자격 충족으로 간주하지 않는다. */
export function eligibilityDisplayStatus(
  conditions: readonly EligibilityStatusItem[] | null | undefined,
): EligibilityDisplayStatus {
  if (conditions?.some((condition) => condition.status === 'not_met')) {
    return 'not_met';
  }
  if (conditions?.some((condition) => condition.status === 'needs_review')) {
    return 'needs_review';
  }
  if (!conditions || conditions.length === 0) {
    return 'unknown';
  }
  return 'met';
}

/** 조합의 모든 정책을 확인해야 조합 전체를 충족으로 표시할 수 있다. */
export function portfolioEligibilityStatus(
  policies: readonly { eligibility?: readonly EligibilityStatusItem[] | null }[],
): EligibilityDisplayStatus {
  if (policies.length === 0) {
    return 'unknown';
  }
  const statuses = policies.map((policy) => eligibilityDisplayStatus(policy.eligibility));
  if (statuses.includes('not_met')) {
    return 'not_met';
  }
  if (statuses.includes('needs_review')) {
    return 'needs_review';
  }
  if (statuses.includes('unknown')) {
    return 'unknown';
  }
  return 'met';
}

type ExcludedItem = NonNullable<ResultView['excluded']>[number];
export interface PortfolioExclusion {
  policy_id: ExcludedItem['policy_id'];
  amount_won: number | null;
  amount_breakdown?: NonNullable<Portfolio['given_up']>[number]['amount_breakdown'];
  reason: string | null;
  evidence: ExcludedItem['evidence'][number][];
}

/** 제외 여부·금액은 선택한 조합의 서버 응답을 쓰고, 중복 제한 근거는 같은 추천 응답의 요약에서 연결한다. */
export function portfolioExclusions(
  view: ResultView,
  selected: Portfolio | undefined,
  info: PolicySummaries,
): PortfolioExclusion[] {
  if (selected === undefined) {
    return [];
  }
  const selectedIds = new Set(selected.policies.map((policy) => policy.policy_id));
  const givenUp = selected.given_up ?? [];
  const ids = new Set(givenUp.map((item) => item.policy_id));
  // view.excluded의 설명은 추천 조합 기준이다. 대안 조합에 그대로 재사용하지 않는다.
  if (selected.rank === 1) {
    view.excluded?.forEach((item) => ids.add(item.policy_id));
  }
  return [...ids].filter((id) => !selectedIds.has(id)).map((id) => {
    const givenUpItem = givenUp.find((item) => item.policy_id === id);
    const detail = selected.rank === 1 ? view.excluded?.find((item) => item.policy_id === id) : undefined;
    const evidence = detail?.evidence ?? [
      ...(info.get(id)?.conflicts ?? [])
        .filter((conflict) => selectedIds.has(conflict.policy_id))
        .flatMap((conflict) => conflict.evidence),
      ...selected.policies.flatMap((policy) =>
        (info.get(policy.policy_id)?.conflicts ?? [])
          .filter((conflict) => conflict.policy_id === id)
          .flatMap((conflict) => conflict.evidence),
      ),
    ];
    return {
      policy_id: id,
      amount_won: givenUpItem?.amount_won ?? null,
      ...(givenUpItem?.amount_breakdown === undefined ? {} : { amount_breakdown: givenUpItem.amount_breakdown }),
      reason: detail?.reason ?? null,
      evidence,
    };
  });
}

export function pickView(result: RecommendationResponse, period: Period, presentValue: boolean): ResultView {
  const periodResult = result.results[period];
  return presentValue ? periodResult.pv_on : periodResult.pv_off;
}

/** 예전 응답에도 상세 요청 없이 표시용 이름을 찾아 쓴다. */
export function policySummaries(result: RecommendationResponse | null): PolicySummaries {
  return new Map(Object.entries(result?.policy_summaries ?? {}));
}

/** 요약이 없는 이전 응답의 정책은 ID로 식별한다. */
export function policyName(id: string, info: PolicySummaries): string {
  return info.get(id)?.title ?? `정책 ${shortPolicyId(id)}`;
}

/** 배지 이름: 추천 조합 / 대안 1 / 대안 2 */
export function portfolioLabel(rank: number): string {
  return rank === 1 ? '추천 조합' : `대안 ${String(rank - 1)}`;
}
