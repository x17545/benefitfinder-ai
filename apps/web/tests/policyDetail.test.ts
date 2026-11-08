import { describe, expect, it } from 'vitest';
import benefitText from '../../../contracts/fixtures/youth-benefit.json?raw';
import resultText from '../../../contracts/fixtures/recommendation-response.json?raw';
import type { NormalizedBenefit } from '../src/shared/api/benefits.ts';
import type { RecommendationResponse } from '../src/shared/api/recommendations.ts';
import {
  amountSummary,
  applyPeriod,
  conditionQuotes,
  diagnosisSummary,
  findMyPolicyResult,
} from '../src/features/policy/policyDetailModel.ts';
import type { ConditionResult } from '../src/features/policy/policyDetailModel.ts';
import { loadPolicyDetail, toDetailState } from '../src/features/policy/usePolicyDetail.ts';

const benefit = JSON.parse(benefitText) as NormalizedBenefit;
const result = JSON.parse(resultText) as RecommendationResponse;

function condition(id: string, status: ConditionResult['status']): ConditionResult {
  return { condition_id: id, status, reason: '', evidence: [] };
}

function respond(status: number, body: unknown): typeof fetch {
  return () => Promise.resolve(new Response(JSON.stringify(body), { status }));
}

describe('S5 정책 상세 글자', () => {
  it('진단 요약은 충족·확인 필요·미충족 개수를 센다 (문구 S5-SUM)', () => {
    expect(diagnosisSummary([condition('age', 'met'), condition('income', 'met'), condition('parent_income', 'needs_review')]))
      .toBe('조건 3개 중 2개 충족, 1개 확인 필요');
    expect(diagnosisSummary([condition('age', 'met'), condition('income', 'not_met')])).toBe('조건 2개 중 1개 충족, 1개 미충족');
  });

  it('신청 기간은 날짜가 없으면 추정하지 않는다', () => {
    expect(applyPeriod({ status: 'open', starts_on: '2026-10-01', ends_on: '2026-12-31', url: null })).toBe('2026.10.01 ~ 2026.12.31');
    expect(applyPeriod({ status: 'unknown', starts_on: null, ends_on: null, url: null })).toBe('공식 페이지에서 확인해 주세요');
  });

  it('지원 내용은 원문 요약을 먼저 쓴다', () => {
    expect(amountSummary(benefit.benefit_amount)).toBe('[가상] 월 최대 20만 원, 최장 12개월');
    expect(amountSummary(null)).toContain('공식 페이지');
  });

  it('원문 인용은 정책 상세의 조건 근거를 쓴다', () => {
    expect(conditionQuotes(benefit, condition('age', 'met'))[0]?.quote).toContain('만 19세 이상 34세 이하');
  });

  it('마지막 분석 결과에서 이 정책의 자격 진단을 찾는다', () => {
    const id = result.results['1y'].pv_off.portfolios[0]?.policies[0]?.policy_id ?? '';
    expect(findMyPolicyResult(result, id)?.eligibility.length).toBeGreaterThan(0);
    expect(findMyPolicyResult(result, 'youth:example:none')).toBeNull();
    expect(findMyPolicyResult(null, id)).toBeNull();
  });
});

describe('S5 정책 상세 받기', () => {
  it('404는 없는 정책, 다른 실패와 모양이 틀린 응답은 오류로 구분한다', async () => {
    expect((await loadPolicyDetail('x', respond(200, benefit))).status).toBe('ok');
    expect((await loadPolicyDetail('x', respond(404, {}))).status).toBe('notFound');
    expect((await loadPolicyDetail('x', respond(500, {}))).status).toBe('error');
    expect((await loadPolicyDetail('x', respond(200, { id: 'x' }))).status).toBe('error');
  });

  it('개발 서버에서 API가 없으면 가상 예시를 쓰고, 예시에 없는 정책은 그대로 둔다', () => {
    const sample = toDetailState('youth:example:P1', { status: 'notFound' });
    expect(sample.status === 'ok' && sample.isSample).toBe(true);
    expect(toDetailState('youth:example:unknown', { status: 'notFound' }).status).toBe('notFound');
  });
});
