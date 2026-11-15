import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { NormalizedBenefit } from '../src/shared/api/benefits.ts';

const detail = vi.hoisted(() => ({ benefit: null as NormalizedBenefit | null }));

vi.mock('../src/features/policy/usePolicyDetail.ts', () => ({
  usePolicyDetail: () => ({
    state: { status: 'ok', benefit: detail.benefit, isSample: false },
    retry: vi.fn(),
  }),
}));
vi.mock('../src/features/benefits/analysisStore.ts', () => ({
  useAnalysis: () => ({ status: 'idle' }),
}));
vi.mock('../src/features/profile/flowStore.ts', () => ({
  useFlowState: () => ({ consent: false }),
}));
vi.mock('../src/features/benefits/policyInfo.ts', () => ({
  usePolicyInfo: () => new Map(),
}));

import { PolicyDetailScreen } from '../src/features/policy/PolicyDetailScreen.tsx';

type Application = NormalizedBenefit['application'];

const APPLICATION_URL = 'https://apply.example.test/P1';
const SOURCE_URL = 'https://source.example.test/P1';

function render(application: Application): string {
  detail.benefit = {
    id: 'youth:test:P1',
    domain: 'youth',
    title: '가상 청년 정책',
    summary: '회귀 테스트용 가상 정책',
    provider: '가상 기관',
    categories: ['housing'],
    application,
    eligibility_conditions: [],
    source: {
      url: SOURCE_URL,
      checked_at: '2026-11-01T09:00:00+09:00',
      document_version: 'test-v1',
    },
    domain_data: {},
    virtual_data: false,
  };
  return renderToStaticMarkup(createElement(PolicyDetailScreen, { policyId: detail.benefit.id }));
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-11-13T12:00:00+09:00'));
  vi.stubGlobal('window', { location: { search: '' }, history: { length: 2 } });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('PolicyDetailScreen recruitment action', () => {
  it('offers the application link while recruitment is open', () => {
    const html = render({
      status: 'open', starts_on: '2026-11-01', ends_on: '2026-11-30', url: APPLICATION_URL,
    });

    expect(html).toContain('모집 중');
    expect(html).toContain('신청하러 가기 ↗');
    expect(html).toContain(`href="${APPLICATION_URL}"`);
    expect(html).not.toContain('지금은 신청 기간이 아니에요');
  });

  it('treats an open status past its end date as closed', () => {
    const html = render({
      status: 'open', starts_on: '2026-11-01', ends_on: '2026-11-12', url: APPLICATION_URL,
    });

    expect(html).toContain('모집 끝남');
    expect(html).toContain('지금은 신청 기간이 아니에요');
    expect(html).toContain('공식 페이지 보기 ↗');
    expect(html).toContain(`href="${SOURCE_URL}"`);
    expect(html).not.toContain('신청하러 가기');
    expect(html).not.toContain(APPLICATION_URL);
  });

  it('uses the official page before a future start date', () => {
    const html = render({
      status: 'open', starts_on: '2026-11-14', ends_on: '2026-11-30', url: APPLICATION_URL,
    });

    expect(html).toContain('모집 예정');
    expect(html).toContain('공식 페이지 보기 ↗');
    expect(html).toContain(`href="${SOURCE_URL}"`);
    expect(html).not.toContain('신청하러 가기');
    expect(html).not.toContain(APPLICATION_URL);
    expect(html).not.toContain('지금은 신청 기간이 아니에요');
  });

  it('preserves the original meaning of unknown and dateless open statuses', () => {
    const unknown = render({ status: 'unknown', starts_on: null, ends_on: null, url: APPLICATION_URL });
    expect(unknown).toContain('일정 확인 필요');
    expect(unknown).toContain('공식 페이지 보기 ↗');
    expect(unknown).toContain(`href="${SOURCE_URL}"`);
    expect(unknown).not.toContain('신청하러 가기');

    const open = render({ status: 'open', starts_on: null, ends_on: null, url: APPLICATION_URL });
    expect(open).toContain('모집 중');
    expect(open).toContain('신청하러 가기 ↗');
    expect(open).toContain(`href="${APPLICATION_URL}"`);
  });
});
