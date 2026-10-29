// 서버 결과에서 화면에 필요한 값만 꺼낸다. 순위·금액은 서버가 계산한 값을 그대로 쓴다.
import type { NormalizedBenefit } from '../../shared/api/benefits.ts';
import type { RecommendationResponse } from '../../shared/api/recommendations.ts';
import { shortPolicyId } from './format.ts';
import type { Period } from './format.ts';

export type ResultView = RecommendationResponse['results']['1y']['pv_off'];
export type Portfolio = ResultView['portfolios'][number];
export type PortfolioPolicy = Portfolio['policies'][number];

export function pickView(result: RecommendationResponse, period: Period, presentValue: boolean): ResultView {
  const periodResult = result.results[period];
  return presentValue ? periodResult.pv_on : periodResult.pv_off;
}

/** 결과 안에 나오는 모든 정책 id (이름을 받아 오려고) */
export function allPolicyIds(result: RecommendationResponse): string[] {
  const ids = new Set<string>();
  for (const period of Object.values(result.results)) {
    for (const view of [period.pv_off, period.pv_on]) {
      for (const portfolio of view.portfolios) {
        portfolio.policies.forEach((policy) => ids.add(policy.policy_id));
        portfolio.given_up?.forEach((item) => ids.add(item.policy_id));
      }
      view.why?.deciding_conflict?.forEach((id) => ids.add(id));
      view.excluded?.forEach((item) => ids.add(item.policy_id));
      view.not_eligible?.forEach((item) => ids.add(item.policy_id));
      view.not_yet_reviewed?.forEach((item) => ids.add(item.policy_id));
      view.closed_now?.forEach((item) => ids.add(item.policy_id));
      view.fallback_list?.forEach((item) => ids.add(item.policy_id));
    }
  }
  return [...ids];
}

/** 정책 이름. 상세를 아직 못 받았으면 id 끝부분 */
export function policyName(id: string, info: ReadonlyMap<string, NormalizedBenefit>): string {
  return info.get(id)?.title ?? `정책 ${shortPolicyId(id)}`;
}

/** 배지 이름: 추천 조합 / 대안 1 / 대안 2 */
export function portfolioLabel(rank: number): string {
  return rank === 1 ? '추천 조합' : `대안 ${String(rank - 1)}`;
}
