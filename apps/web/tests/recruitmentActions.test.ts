import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FallbackList } from '../src/features/benefits/FallbackList.tsx';
import { PolicyCard } from '../src/features/benefits/PolicyCard.tsx';
import type { PortfolioPolicy } from '../src/features/benefits/resultModel.ts';
import type { NormalizedBenefit } from '../src/shared/api/benefits.ts';

type Application = NormalizedBenefit['application'];

const APPLICATION_URL = 'https://apply.example.test/P1';
const SOURCE_URL = 'https://source.example.test/P1';

function benefit(application: Application, virtualData = false): NormalizedBenefit {
  return {
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
    virtual_data: virtualData,
  };
}

const policy: PortfolioPolicy = {
  policy_id: 'youth:test:P1',
  amount_in_period_won: 100_000,
  original_amount_won: 100_000,
  amount_status: 'verified',
  eligibility: [],
  ai_reason: null,
  ai_generated: false,
  source: {
    url: SOURCE_URL,
    checked_at: '2026-11-01T09:00:00+09:00',
    document_version: 'test-v1',
  },
};

function renderPolicyCard(current: NormalizedBenefit | undefined, virtualData = false): string {
  return renderToStaticMarkup(createElement(PolicyCard, {
    policy,
    info: current === undefined ? new Map() : new Map([[current.id, current]]),
    detailHref: '/policy/youth:test:P1',
    virtualData,
    aiGenerated: false,
  }));
}

function renderFallback(current: NormalizedBenefit | undefined): string {
  return renderToStaticMarkup(createElement(FallbackList, {
    items: [{ policy_id: policy.policy_id, amount_in_period_won: policy.amount_in_period_won }],
    info: current === undefined ? new Map() : new Map([[current.id, current]]),
  }));
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-11-13T12:00:00+09:00'));
});

afterEach(() => {
  vi.useRealTimers();
});

describe.each([
  ['PolicyCard', renderPolicyCard],
  ['FallbackList', renderFallback],
] as const)('%s recruitment action', (_name, render) => {
  it('offers the application link only while recruitment is open', () => {
    const html = render(benefit({
      status: 'open', starts_on: '2026-11-13', ends_on: '2026-11-27', url: APPLICATION_URL,
    }));
    expect(html).toContain('신청하러 가기 ↗');
    expect(html).toContain(`href="${APPLICATION_URL}"`);
    expect(html).not.toContain('공식 페이지 보기');
  });

  it.each([
    ['expired open', {
      status: 'open', starts_on: '2026-11-01', ends_on: '2026-11-12', url: APPLICATION_URL,
    }],
    ['explicit closed', {
      status: 'closed', starts_on: '2026-11-01', ends_on: '2026-12-31', url: APPLICATION_URL,
    }],
    ['upcoming', {
      status: 'open', starts_on: '2026-11-14', ends_on: '2026-12-31', url: APPLICATION_URL,
    }],
    ['unknown', {
      status: 'unknown', starts_on: null, ends_on: null, url: APPLICATION_URL,
    }],
  ] satisfies [string, Application][])('shows the official page for %s', (_case, application) => {
    const html = render(benefit(application));
    expect(html).toContain('공식 페이지 보기 ↗');
    expect(html).toContain(`href="${SOURCE_URL}"`);
    expect(html).not.toContain('신청하러 가기');
    expect(html).not.toContain(`href="${APPLICATION_URL}"`);
  });

  it('does not infer an application action when policy information is missing', () => {
    const html = render(undefined);
    expect(html).not.toContain('신청하러 가기');
    expect(html).not.toContain('공식 페이지 보기');
  });

  it('keeps virtual policy action hidden', () => {
    const html = render(benefit({
      status: 'open', starts_on: '2026-11-13', ends_on: '2026-11-27', url: APPLICATION_URL,
    }, true));
    expect(html).not.toContain('신청하러 가기');
    expect(html).not.toContain('공식 페이지 보기');
  });
});
