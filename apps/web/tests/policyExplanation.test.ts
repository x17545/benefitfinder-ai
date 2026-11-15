import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { PolicyCard } from '../src/features/benefits/PolicyCard.tsx';
import { PolicyExplanation } from '../src/features/benefits/PolicyExplanation.tsx';
import type { PortfolioPolicy } from '../src/features/benefits/resultModel.ts';

// 가상값만 직접 구성한다. 실제 모델 출력과 로컬 결과, 실제 원문은 복사하지 않는다.
const CHECKED_AT = '2026-10-26T09:00:00+09:00';

function source(url: string, version = 'fictional-v1'): PortfolioPolicy['source'] {
  return { url, checked_at: CHECKED_AT, document_version: version };
}

function aiSource(
  id: string,
  quote: string,
  url = 'https://policy.example/youth/virtual-evidence',
): NonNullable<PortfolioPolicy['ai_sources']>[number] {
  return {
    quote,
    location: id,
    source: source(url),
  };
}

function policy(overrides: Partial<PortfolioPolicy> = {}): PortfolioPolicy {
  return {
    policy_id: 'youth:example:P90',
    eligibility: [],
    source: source('https://policy.example/youth/virtual-p90'),
    ...overrides,
  };
}

function renderExplanation(p: PortfolioPolicy, aiGenerated: boolean): string {
  return renderToStaticMarkup(createElement(PolicyExplanation, { policy: p, aiGenerated }));
}

describe('PolicyExplanation grounded rendering', () => {
  it('성공 응답은 AI 배지와 원문 근거 2개를 그대로 보여준다', () => {
    const first = aiSource(
      '가상 공고: 지원 대상',
      '[가상 예제] 가상 지원 대상은 만 19세 이상 34세 이하입니다.',
      'https://policy.example/youth/virtual-p90-a',
    );
    const second = aiSource(
      '가상 공고: 신청 안내',
      '[가상 예제] 온라인 신청서와 가상 계획서를 제출합니다.',
      'https://policy.example/youth/virtual-p90-b',
    );
    const reason = '[가상] 가상 조건에 맞는 지원금입니다. 1000000원.';
    const html = renderExplanation(
      policy({ ai_reason: reason, ai_generated: true, ai_sources: [first, second] }),
      false,
    );

    expect(html).toContain(reason);
    expect(html).toContain('AI');
    expect(html).not.toContain('기본 안내');
    expect(html).toContain('<details');
    expect(html).toContain('<summary');
    expect(html).toContain('원문 근거 2개');
    expect(html.split('<blockquote').length - 1).toBe(2);
    expect(html).toContain('[가상 예제] 가상 지원 대상은');
    expect(html).toContain('가상 공고: 지원 대상');
    expect(html).toContain('가상 공고: 신청 안내');
    expect(html).toContain('fictional-v1');
    expect(html).toContain('<time');
    expect(html).toContain(CHECKED_AT);
    expect(html.toLowerCase()).toContain('datetime');
    expect(html).toContain('https://policy.example/youth/virtual-p90-a');
    expect(html).toContain('https://policy.example/youth/virtual-p90-b');
    expect(html).toContain('target="_blank"');
    expect(html).toContain('rel="noopener noreferrer"');
  });

  it('오류와 시간초과 가상 응답의 기본 문구는 기본 안내만 보여준다', () => {
    const reason = '[가상] 가상 정책 P90은 확인이 필요합니다. 세부 자격과 신청 방법은 공식 공고에서 확인해 주세요.';
    const html = renderExplanation(
      policy({ ai_reason: reason, ai_generated: false, ai_sources: [] }),
      false,
    );

    expect(html).toContain(reason);
    expect(html).toContain('기본 안내');
    expect(html).not.toContain('>AI<');
    expect(html).not.toContain('<details');
    expect(html).not.toContain('<summary');
  });

  it('부분 실패에서 최상위 true라도 정책 false면 기본 안내를 유지한다', () => {
    const reason = '[가상] 가상 정책 P90은 확인이 필요합니다. 세부 자격과 신청 방법은 공식 공고에서 확인해 주세요.';
    const html = renderExplanation(
      policy({ ai_reason: reason, ai_generated: false, ai_sources: [] }),
      true,
    );

    expect(html).toContain('기본 안내');
    expect(html).not.toContain('>AI<');
    expect(html).not.toContain('<details');
  });

  it('legacy 정책은 per-policy 플래그 없이 최상위 값으로 배지를 정한다', () => {
    const reason = '[가상] 가상 월세 부담을 덜어주는 조합입니다.';
    const legacy = policy({ ai_reason: reason });

    const aiHtml = renderExplanation(legacy, true);
    expect(aiHtml).toContain(reason);
    expect(aiHtml).toContain('AI');
    expect(aiHtml).not.toContain('기본 안내');
    expect(aiHtml).not.toContain('<details');

    const defaultHtml = renderExplanation(legacy, false);
    expect(defaultHtml).toContain(reason);
    expect(defaultHtml).toContain('기본 안내');
    expect(defaultHtml).not.toContain('>AI<');
    expect(defaultHtml).not.toContain('<details');
  });

  it('근거가 없으면 펼침을 만들지 않는다', () => {
    const reason = '[가상] 가상 조건에 맞는 지원금입니다.';
    const missingHtml = renderExplanation(
      policy({ ai_reason: reason, ai_generated: true, ai_sources: undefined }),
      true,
    );
    expect(missingHtml).toContain(reason);
    expect(missingHtml).not.toContain('<details');
    expect(missingHtml).not.toContain('<summary');

    const emptyHtml = renderExplanation(
      policy({ ai_reason: reason, ai_generated: false, ai_sources: [] }),
      false,
    );
    expect(emptyHtml).not.toContain('<details');
    expect(emptyHtml).not.toContain('<summary');

    const falseMissingHtml = renderExplanation(
      policy({ ai_reason: reason, ai_generated: false }),
      false,
    );
    expect(falseMissingHtml).toContain(reason);
    expect(falseMissingHtml).not.toContain('<details');
  });

  it('빈 설명은 배지와 근거를 만들지 않는다', () => {
    for (const empty of ['', null, undefined] as const) {
      const html = renderExplanation(policy({ ai_reason: empty, ai_generated: false }), false);
      expect(html).not.toContain('기본 안내');
      expect(html).not.toContain('>AI<');
      expect(html).not.toContain('<details');
      expect(html).not.toContain('<summary');
    }
  });

  it('HTML을 렌더하지 않고 이스케이프한다', () => {
    const reason = '[가상] <b>강조</b>와 <script>alert(1)</script> 포함 설명';
    const quote = '[가상 예제] <script>alert(1)</script> 원문 인용';
    const html = renderExplanation(
      policy({
        ai_reason: reason,
        ai_generated: true,
        ai_sources: [aiSource('가상 공고: 지원 대상', quote)],
      }),
      true,
    );
    expect(html).toContain('&lt;script&gt;');
    expect(html).not.toContain('<script>alert(1)</script>');
    expect(html).toContain('&lt;b&gt;');
    expect(html).not.toContain('<b>강조</b>');
    expect(html).toContain('<blockquote');
  });
});

describe('PolicyCard evidence placement', () => {
  it('근거 details와 외부 원문 링크를 내부 정책 링크 바깥에 둔다', () => {
    const evidenceUrl = 'https://policy.example/youth/virtual-evidence-outside';
    const grounded = policy({
      ai_reason: '[가상] 가상 조건에 맞는 지원금입니다.',
      ai_generated: true,
      ai_sources: [aiSource('가상 공고: 지원 대상', '[가상 예제] 가상 대상 원문입니다.', evidenceUrl)],
    });
    const html = renderToStaticMarkup(
      createElement(PolicyCard, { policy: grounded, info: new Map(), rank: 1, aiGenerated: false, detailHref: '/policy/sample', virtualData: false }),
    );

    expect(html).toContain('[가상] 가상 조건에 맞는 지원금입니다.');
    expect(html).toContain('<details');
    expect(html).toContain('원문 근거 1개');
    expect(html).toContain(evidenceUrl);

    const mainStart = html.indexOf('s3-policy__main');
    expect(mainStart).toBeGreaterThan(-1);
    const anchorStart = html.lastIndexOf('<a', mainStart);
    const firstClose = html.indexOf('</a>', mainStart);
    expect(anchorStart).toBeGreaterThan(-1);
    expect(firstClose).toBeGreaterThan(anchorStart);
    const mainLink = html.slice(anchorStart, firstClose);
    expect(mainLink).not.toContain('<details');
    expect(mainLink).not.toContain(evidenceUrl);
    expect(mainLink).not.toContain('target="_blank"');

    const detailsAt = html.indexOf('<details');
    const evidenceAt = html.indexOf(evidenceUrl);
    expect(detailsAt).toBeGreaterThan(firstClose);
    expect(evidenceAt).toBeGreaterThan(firstClose);
    expect(html).toContain('target="_blank"');
    expect(html).toContain('rel="noopener noreferrer"');
  });
});
