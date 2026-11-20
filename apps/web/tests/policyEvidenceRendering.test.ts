import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import benefitText from '../../../contracts/fixtures/youth-benefit.json?raw';
import resultText from '../../../contracts/fixtures/recommendation-response.json?raw';
import type { NormalizedBenefit } from '../src/shared/api/benefits.ts';
import type { RecommendationResponse } from '../src/shared/api/recommendations.ts';

const mocked = vi.hoisted(() => ({
  benefit: null as unknown,
  result: null as unknown,
}));

vi.mock('../src/features/policy/usePolicyDetail.ts', () => ({
  usePolicyDetail: () => ({
    state: { status: 'ok', benefit: mocked.benefit, isSample: false },
    retry: vi.fn(),
  }),
}));

vi.mock('../src/features/benefits/analysisStore.ts', () => ({
  useAnalysis: () => ({
    status: 'done',
    result: mocked.result,
    isSample: false,
  }),
}));

vi.mock('../src/features/profile/flowStore.ts', () => ({
  useFlowState: () => ({ consent: true }),
}));

import { PolicyDetailScreen } from '../src/features/policy/PolicyDetailScreen.tsx';

function renderPolicy(includeAge: boolean): string {
  const result = JSON.parse(resultText) as RecommendationResponse;
  const benefit = JSON.parse(benefitText) as NormalizedBenefit;

  const policy = result.results['1y'].pv_off.portfolios[0]?.policies[0];
  if (policy === undefined) throw new Error('추천 fixture에 정책이 없습니다.');

  const source = policy.source;
  const items = [
    ...Array.from({ length: 10 }, (_, index) => ({
      condition_id: 'official_source_terms_' + String(index),
      status: 'needs_review' as const,
      reason: '입력 정보나 기준 적용을 확인해야 해요.',
      evidence: [{
        quote: '공식 안내 구간 ' + String(index),
        location: 'chars:0-10',
        source,
      }],
    })),
    {
      condition_id: 'calculated_component',
      status: 'needs_review' as const,
      reason: '금액 산정 기준 확인 필요',
      evidence: [{
        quote: '기본 구직촉진수당 계산 근거',
        location: 'chars:10-20',
        source,
      }],
    },
  ];

  if (includeAge) {
    items.push({
      condition_id: 'age',
      status: 'needs_review',
      reason: '나이 기준 확인 필요',
      evidence: [{
        quote: '연령 조건 근거',
        location: 'chars:20-30',
        source,
      }],
    });
  }

  Object.assign(policy, { eligibility: items });
  Object.assign(result, { virtual_data: false });
  Object.assign(benefit, {
    id: policy.policy_id,
    source,
    virtual_data: false,
    eligibility_conditions: items.map((item) => ({
      id: item.condition_id,
      description: item.reason,
      evidence: item.evidence,
    })),
  });

  mocked.benefit = benefit;
  mocked.result = result;

  vi.stubGlobal('window', {
    location: { search: '' },
    history: { length: 2 },
  });

  return renderToStaticMarkup(
    createElement(PolicyDetailScreen, { policyId: benefit.id }),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  mocked.benefit = null;
  mocked.result = null;
});

describe('정책 상세 공식 근거 렌더링', () => {
  it('10개 원문 구간과 계산 근거를 접기 영역으로 분리한다', () => {
    const html = renderPolicy(false);

    expect(html).toContain('공식 신청 기준 확인이 필요해요');
    expect(html).toContain('공식 안내 및 유의사항 · 원문 10개 구간');
    expect(html).toContain('지원금 계산 근거 · 1개 항목');
    expect(html).toContain('<details');
    expect(html).toContain('공식 안내 구간 0');
    expect(html).toContain('공식 안내 구간 9');
    expect(html).toContain('기본 구직촉진수당 계산 근거');
    expect(html).not.toContain('조건 11개 중');
  });

  it('하단에 원문 구간 11개를 개별 확인 사항으로 반복하지 않는다', () => {
    const html = renderPolicy(false);

    expect(html).toContain('확인이 필요한 것');
    expect(html).toContain('위의 공식 안내와 계산 근거는 신청 전에 확인해 주세요.');
    expect(html).not.toContain('신청 전에 아래 11가지를');
    expect(html).not.toContain('조건 11개 중 0개 충족');
  });

  it('일반 자격 조건은 확인 필요 상태로 유지한다', () => {
    const html = renderPolicy(true);

    expect(html).toContain('조건 1개 중 0개 충족, 1개 확인 필요');
    expect(html).toContain('나이 기준 확인 필요');
    expect(html).toContain('신청 전에 아래 1가지를');
    expect(html).toContain('공식 안내 및 유의사항 · 원문 10개 구간');
  });
});
