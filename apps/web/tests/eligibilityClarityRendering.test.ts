import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { FallbackList } from '../src/features/benefits/FallbackList.tsx';
import { PortfolioCard } from '../src/features/benefits/PortfolioCard.tsx';
import type { Portfolio } from '../src/features/benefits/resultModel.ts';
import type { ConditionResult } from '../src/shared/api/generated/recommendation-response.ts';
import { isRecommendationResponse } from '../src/shared/api/recommendations.ts';
import fixtureText from '../../../contracts/fixtures/recommendation-response.json?raw';

const parsed: unknown = JSON.parse(fixtureText);
if (!isRecommendationResponse(parsed)) {
  throw new Error('invalid recommendation fixture');
}
function fixturePortfolio(): Portfolio {
  if (!isRecommendationResponse(parsed)) {
    throw new Error('invalid recommendation fixture');
  }
  const selected = parsed.results['1y'].pv_off.portfolios[0];
  if (selected === undefined) {
    throw new Error('fixture portfolio is missing');
  }
  return selected;
}

function condition(status: ConditionResult['status']): ConditionResult {
  return { condition_id: 'age', status, reason: '테스트 조건', evidence: [] };
}

function renderFallback(eligibility?: ConditionResult[]): string {
  return renderToStaticMarkup(createElement(FallbackList, {
    items: [{ policy_id: 'youth:test:one', amount_in_period_won: 3_600_000, eligibility }],
    info: new Map(),
    virtualData: true,
  }));
}

function renderPortfolio(status: ConditionResult['status']): string {
  const base = fixturePortfolio();
  const portfolio: Portfolio = {
    ...base,
    policies: base.policies.map((policy) => ({
      ...policy,
      eligibility: [condition(status)],
    })) as Portfolio['policies'],
  };
  return renderToStaticMarkup(createElement(PortfolioCard, {
    portfolio,
    topTotal: portfolio.total_won,
    selected: true,
    onSelect: () => undefined,
    info: new Map(),
  }));
}

describe('자격 상태별 지원금 카드 안내', () => {
  it('확인 필요 정책의 개별 금액은 조건부 예상액으로 안내한다', () => {
    expect(renderFallback([condition('needs_review')])).toContain('자격 충족 시 예상 금액');
  });

  it('자격 진단이 없는 개별 정책도 충족으로 단정하지 않는다', () => {
    expect(renderFallback()).toContain('자격 충족 시 예상 금액');
  });

  it('입력 조건상 충족한 개별 정책은 구분해서 안내한다', () => {
    const html = renderFallback([condition('met')]);
    expect(html).toContain('입력 조건상 충족 · 예상 금액');
    expect(html).not.toContain('자격 충족 시 예상 금액 · 확인 필요');
  });

  it('미충족 정책은 참고용 금액이라고 안내한다', () => {
    expect(renderFallback([condition('not_met')])).toContain('미충족 조건 있음 · 금액은 참고용');
  });

  it('확인 필요 정책이 포함된 조합은 조건부 총액이라고 안내한다', () => {
    expect(renderPortfolio('needs_review')).toContain('자격 확인 필요 · 조건부 예상 총액');
  });

  it('모든 정책의 입력 조건이 충족된 조합에는 확인 필요 경고를 붙이지 않는다', () => {
    expect(renderPortfolio('met')).not.toContain('자격 확인 필요 · 조건부 예상 총액');
  });
});
