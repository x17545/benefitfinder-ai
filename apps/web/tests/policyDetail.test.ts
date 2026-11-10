import { describe, expect, it, vi } from 'vitest';
import benefitText from '../../../contracts/fixtures/youth-benefit.json?raw';
import resultText from '../../../contracts/fixtures/recommendation-response.json?raw';
import type { NormalizedBenefit } from '../src/shared/api/benefits.ts';
import type { RecommendationResponse } from '../src/shared/api/recommendations.ts';
import {
  amountSummary,
  applyPeriod,
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
    expect(amountSummary({ max_total_won: 100_000, amount_status: 'unknown' })).toBe('금액 확인 필요');
  });

  it('같은 선택 기준과 정책 버전의 진단·설명만 연결한다', () => {
    const fixture = structuredClone(result);
    const basis = { period: '2y', present_value: true } as const;
    const policy = fixture.results['2y'].pv_on.portfolios[0]?.policies[0];
    if (!policy) throw new Error('invalid fixture');
    fixture.virtual_data = false;
    fixture.ai_generated = true;
    policy.ai_reason = '2년 현재가치 기본 문구';
    policy.ai_generated = false;
    const matching = { ...benefit, id: policy.policy_id, source: policy.source };
    expect(findMyPolicyResult(fixture, matching, basis, false, false)).toMatchObject({
      ai_reason: '2년 현재가치 기본 문구', ai_generated: false, eligibility: policy.eligibility,
    });
    expect(findMyPolicyResult(fixture, { ...matching, id: 'other' }, basis, false, false)).toBeNull();
    const revised = { ...matching, source: { ...matching.source, document_version: 'new-version' } };
    expect(findMyPolicyResult(fixture, revised, basis, false, false)).toBeNull();
    expect(findMyPolicyResult(fixture, matching, basis, true, false)).toBeNull();
    expect(findMyPolicyResult(fixture, matching, basis, false, true)).toBeNull();
  });
});

describe('S5 정책 상세 받기', () => {
  it('404는 없는 정책, 다른 실패와 모양이 틀린 응답은 오류로 구분한다', async () => {
    expect((await loadPolicyDetail(benefit.id, respond(200, benefit))).status).toBe('ok');
    expect((await loadPolicyDetail('x', respond(200, benefit))).status).toBe('error');
    expect((await loadPolicyDetail('x', respond(404, {}))).status).toBe('notFound');
    expect((await loadPolicyDetail('x', respond(500, {}))).status).toBe('error');
    expect((await loadPolicyDetail('x', respond(200, { id: 'x' }))).status).toBe('error');
  });

  it('개발 서버에서 API가 없으면 가상 예시를 쓰고, 예시에 없는 정책은 그대로 둔다', () => {
    const sample = toDetailState('youth:example:P1', { status: 'notFound' });
    expect(sample.status === 'ok' && sample.isSample).toBe(true);
    expect(toDetailState('youth:example:unknown', { status: 'notFound' }).status).toBe('notFound');
    expect(toDetailState('youth:example:P1', { status: 'error' }).status).toBe('error');
  });

  it('배포 빌드에서는 가상 정책이 반환되지 않는다', () => {
    vi.stubEnv('DEV', false);
    try {
      expect(toDetailState('youth:example:P1', { status: 'notFound' }).status).toBe('notFound');
    } finally {
      vi.unstubAllEnvs();
    }
  });
});
