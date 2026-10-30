import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { ResultLists } from '../src/features/benefits/ResultLists.tsx';
import { pickView, portfolioExclusions } from '../src/features/benefits/resultModel.ts';
import type { Portfolio, ResultView } from '../src/features/benefits/resultModel.ts';
import { isNormalizedBenefit } from '../src/shared/api/benefits.ts';
import { isRecommendationResponse } from '../src/shared/api/recommendations.ts';
import fixtureText from '../../../contracts/fixtures/recommendation-response.json?raw';
import benefitText from '../../../contracts/fixtures/youth-benefit.json?raw';

const result: unknown = JSON.parse(fixtureText);
const benefit: unknown = JSON.parse(benefitText);
if (!isRecommendationResponse(result) || !isNormalizedBenefit(benefit)) {
  throw new Error('invalid synthetic fixtures');
}
const view = pickView(result, '1y', false);
const evidence = view.excluded?.[0]?.evidence ?? [];
const info = new Map([['youth:example:P6', {
  ...benefit,
  id: 'youth:example:P6',
  conflicts: benefit.conflicts?.map((conflict) => ({ ...conflict, policy_id: 'youth:example:P1' })),
}]]);

function portfolio(view: ResultView, rank: number): Portfolio {
  const selected = view.portfolios.find((item) => item.rank === rank);
  if (selected === undefined) {
    throw new Error(`fixture portfolio ${String(rank)} is missing`);
  }
  return selected;
}

describe('selected portfolio exclusions', () => {
  it('keeps the recommended combination exclusion and source reason', () => {
    const excluded = portfolioExclusions(view, view.portfolios[0], new Map());
    expect(excluded).toEqual([{
      policy_id: 'youth:example:P6', amount_won: 1_500_000,
      reason: '[가상] P1과 중복 수혜 불가', evidence,
    }]);
  });

  it('shows P1, not the selected P6, for the P3+P6 alternative and connects reverse conflict evidence', () => {
    const selected = view.portfolios[1];
    const excluded = portfolioExclusions(view, selected, info);
    expect(excluded.map((item) => item.policy_id)).toEqual(['youth:example:P1']);
    expect(excluded[0]).toMatchObject({ amount_won: 2_400_000, reason: null });
    expect(excluded[0].evidence.length).toBeGreaterThan(0);
    const html = renderToStaticMarkup(createElement(ResultLists, { view, selected, info }));
    const fold = html.match(/<details[^>]*>[\s\S]*?<\/details>/)?.[0] ?? '';
    expect(fold).toContain('youth%3Aexample%3AP1');
    expect(fold).not.toContain('youth%3Aexample%3AP6');
    expect(fold).toContain('240만 원');
    expect(fold).not.toContain('P1과 중복 수혜 불가');
  });

  it('does not invent a source when policy details are unavailable', () => {
    const selected = view.portfolios[1];
    const html = renderToStaticMarkup(createElement(ResultLists, { view, selected, info: new Map() }));
    const fold = html.match(/<details[^>]*>[\s\S]*?<\/details>/)?.[0] ?? '';
    expect(fold).toContain('근거는 정책 상세에서 확인');
    expect(fold).not.toContain('(공고 원문 근거)');
  });

  it('does not fall back to the recommended exclusions when an alternative omits given_up', () => {
    const selected = { ...portfolio(view, 2), given_up: undefined };
    expect(portfolioExclusions(view, selected, new Map())).toEqual([]);
    expect(portfolioExclusions(view, undefined, new Map())).toEqual([]);
  });

  it('uses period-specific amounts and keeps zero distinct from missing', () => {
    const twoYears = pickView(result, '2y', false);
    const selected = portfolio(twoYears, 2);
    expect(portfolioExclusions(twoYears, selected, new Map())[0]?.amount_won).toBe(3_000_000);
    const zero = { ...selected, given_up: [{ policy_id: 'youth:example:P6', amount_won: 0 }] };
    expect(portfolioExclusions(twoYears, zero, new Map())[0]?.amount_won).toBe(0);
  });
});
