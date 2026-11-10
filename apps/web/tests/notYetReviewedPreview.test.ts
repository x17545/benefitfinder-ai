import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { ResultLists } from '../src/features/benefits/ResultLists.tsx';
import type { ResultView } from '../src/features/benefits/resultModel.ts';
import { isRecommendationResponse } from '../src/shared/api/recommendations.ts';
import fixtureText from '../../../contracts/fixtures/recommendation-response.json?raw';

// 가상값만 사용한다. 실제 공고·원문·사용자 자료를 쓰지 않는다.
const CHECKED_AT = '2026-10-26T09:00:00+09:00';
const PREVIEW_ID = 'youth:example:P120';
const PREVIEW_NAME = '[가상] 검증용 취업 미리보기';
const PREVIEW_SCOPE = '[가상] 게시 조항 확인 범위, 조합·자격·금액 확정 전';

function previewSource(url = 'https://policy.example/youth/virtual-preview'): Record<string, string> {
  return { url, checked_at: CHECKED_AT, document_version: 'fictional-v1' };
}

function previewExplanation(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    policy_id: PREVIEW_ID,
    amount_in_period_won: null,
    original_amount_won: null,
    amount_status: 'unknown',
    eligibility: [],
    ai_reason: '[가상] 확인 중인 정책 안내입니다. 조합·자격·금액 확정 전입니다.',
    ai_generated: true,
    ai_sources: [
      {
        quote: '[가상 예제] 가상 게시 조항 원문입니다.',
        location: '가상 공고: 지원 대상',
        source: previewSource('https://policy.example/youth/virtual-preview-evidence'),
      },
    ],
    source: previewSource(),
    ...overrides,
  };
}

function previewItem(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    policy_id: PREVIEW_ID,
    eligibility_summary: 'needs_review',
    policy_name: PREVIEW_NAME,
    review_scope: PREVIEW_SCOPE,
    verification_only: true,
    review_explanation: previewExplanation(),
    ...overrides,
  };
}

function responseWithNotReviewed(item: unknown): unknown {
  const root = JSON.parse(fixtureText) as Record<string, unknown>;
  const results = root.results as Record<string, Record<string, Record<string, unknown>>>;
  for (const period of ['1y', '2y', 'all'] as const) {
    for (const toggle of ['pv_off', 'pv_on'] as const) {
      const view = results[period][toggle] as Record<string, unknown>;
      view.not_yet_reviewed = [item];
    }
  }
  return root;
}

function viewWithNotReviewed(items: unknown[]): ResultView {
  return {
    portfolios: [],
    calculation_status: 'available',
    unavailable_reason: null,
    not_yet_reviewed: items as ResultView['not_yet_reviewed'],
  } as ResultView;
}

function renderLists(items: unknown[], aiGenerated = false): string {
  const view = viewWithNotReviewed(items);
  return renderToStaticMarkup(createElement(ResultLists, { view, selected: undefined, info: new Map(), aiGenerated }));
}

describe('not_yet_reviewed preview validation', () => {
  it('기존 응답(설명 없음)을 그대로 받아들인다', () => {
    const root = JSON.parse(fixtureText) as unknown;
    expect(isRecommendationResponse(root)).toBe(true);
  });

  it('가상 preview 구성을 받아들인다', () => {
    expect(isRecommendationResponse(responseWithNotReviewed(previewItem()))).toBe(true);
  });

  it('설명 없이 이름·범위만 있어도 기존 호환을 유지한다', () => {
    const basic = { policy_id: PREVIEW_ID, eligibility_summary: 'needs_review' };
    expect(isRecommendationResponse(responseWithNotReviewed(basic))).toBe(true);
  });

  it('review_explanation이 있으면 이름·범위·검증 고정이 필수다', () => {
    for (const key of ['policy_name', 'review_scope', 'verification_only'] as const) {
      const item = previewItem();
      delete (item as Record<string, unknown>)[key];
      expect(isRecommendationResponse(responseWithNotReviewed(item))).toBe(false);
    }
  });

  it('검증 고정이 true가 아니면 거부한다', () => {
    expect(isRecommendationResponse(responseWithNotReviewed(previewItem({ verification_only: false })))).toBe(false);
  });

  it('설명 policy_id가 바깥과 다르면 거부한다', () => {
    const item = previewItem({ review_explanation: previewExplanation({ policy_id: 'youth:example:P999' }) });
    expect(isRecommendationResponse(responseWithNotReviewed(item))).toBe(false);
  });

  it('확정 금액(정수·verified)이 있으면 거부한다', () => {
    const withAmount = previewItem({ review_explanation: previewExplanation({ amount_in_period_won: 1000000 }) });
    expect(isRecommendationResponse(responseWithNotReviewed(withAmount))).toBe(false);
    const withOriginal = previewItem({ review_explanation: previewExplanation({ original_amount_won: 1000000 }) });
    expect(isRecommendationResponse(responseWithNotReviewed(withOriginal))).toBe(false);
    const verified = previewItem({ review_explanation: previewExplanation({ amount_status: 'verified' }) });
    expect(isRecommendationResponse(responseWithNotReviewed(verified))).toBe(false);
  });

  it('설명에 확정 판정(met/not_met)이 있으면 거부한다', () => {
    const confirmed = previewExplanation({
      eligibility: [
        {
          condition_id: 'age',
          status: 'met',
          reason: '[가상] 가상 충족 근거',
          evidence: [
            {
              quote: '[가상 예제] 가상 연령 조항',
              source: previewSource(),
            },
          ],
        },
      ],
    });
    expect(isRecommendationResponse(responseWithNotReviewed(previewItem({ review_explanation: confirmed })))).toBe(false);
  });

  it('preview가 있는데 바깥 요약이 needs_review가 아니면 거부한다', () => {
    const item = previewItem({ eligibility_summary: 'met' });
    expect(isRecommendationResponse(responseWithNotReviewed(item))).toBe(false);
  });
});

describe('not_yet_reviewed preview rendering', () => {
  it('실제 정책명·검증 표기·범위·AI 출처를 보여준다', () => {
    const html = renderLists([previewItem()]);
    expect(html).toContain(PREVIEW_NAME);
    expect(html).toContain('검증용');
    expect(html).toContain('조합/자격/금액 확정 전');
    expect(html).toContain(PREVIEW_SCOPE);
    expect(html).toContain('AI');
    expect(html).toContain('[가상 예제] 가상 게시 조항 원문입니다.');
    expect(html).toContain('가상 공고: 지원 대상');
    expect(html).toContain('fictional-v1');
    expect(html).toContain(CHECKED_AT);
    expect(html).toContain('https://policy.example/youth/virtual-preview-evidence');
  });

  it('실패 기본 안내는 기본 안내로 떨어지고 원문 펼침을 숨긴다', () => {
    const basicExplanation = previewExplanation({
      ai_reason: '[가상] 가상 정책은 확인이 필요합니다. 세부 자격과 신청 방법은 공식 공고에서 확인해 주세요.',
      ai_generated: false,
      ai_sources: [],
    });
    const html = renderLists([previewItem({ review_explanation: basicExplanation })]);
    expect(html).toContain(PREVIEW_NAME);
    expect(html).toContain('검증용');
    expect(html).toContain('기본 안내');
    expect(html).not.toContain('>AI<');
    expect(html).not.toContain('s3-evidence');
    expect(html).not.toContain('원문 근거 1개');
  });

  it('설명 없는 기존 항목은 기존 동작을 유지한다', () => {
    const html = renderLists([{ policy_id: PREVIEW_ID, eligibility_summary: 'needs_review' }]);
    expect(html).toContain('정책 P120');
    expect(html).toContain('자격 진단');
    expect(html).not.toContain('검증용');
    expect(html).not.toContain('확인 범위:');
    expect(html).not.toContain(PREVIEW_NAME);
  });

  it('수급·신청 보장 CTA를 추가하지 않는다', () => {
    const html = renderLists([previewItem()]);
    expect(html).not.toContain('신청하러 가기');
    expect(html).not.toContain('받을 수 있어요');
    expect(html).not.toContain('수급 가능');
    expect(html).not.toContain('신청 가능');
  });
});
