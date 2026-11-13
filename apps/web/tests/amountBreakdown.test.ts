import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { AmountBreakdown } from '../src/features/benefits/AmountBreakdown.tsx';
import { FallbackList } from '../src/features/benefits/FallbackList.tsx';
import { PolicyCard } from '../src/features/benefits/PolicyCard.tsx';
import { PortfolioCard } from '../src/features/benefits/PortfolioCard.tsx';
import { ResultLists } from '../src/features/benefits/ResultLists.tsx';
import { pickView } from '../src/features/benefits/resultModel.ts';
import { SimulationResult } from '../src/features/whatIf/WhatIfScreen.tsx';
import type { ChangeSimulationResult } from '../src/shared/api/simulations.ts';
import { isRecommendationResponse } from '../src/shared/api/recommendations.ts';
import { isSimulationResult } from '../src/shared/api/simulations.ts';
import fixtureText from '../../../contracts/fixtures/recommendation-response.json?raw';
import maturityText from '../../../contracts/fixtures/maturity-response.json?raw';
import simulationText from '../../../contracts/fixtures/simulation-response.json?raw';

const regular: unknown = JSON.parse(fixtureText);
const maturity: unknown = JSON.parse(maturityText);
const simulation: unknown = JSON.parse(simulationText);
if (!isRecommendationResponse(regular) || !isRecommendationResponse(maturity) || !isSimulationResult(simulation)) {
  throw new Error('invalid synthetic fixtures');
}

const breakdown = { cash_in_period_won: 1_200_000, expected_accrual_won: 3_000_000 };

function expectSeparated(html: string, cash = '120만 원', expected = '300만 원'): void {
  expect(html).toContain('현금 지급 예정(할인 전)');
  expect(html).toContain('만기 전 예상 적립');
  expect(html).toContain(cash);
  expect(html).toContain(expected);
}

describe('F05 amount breakdown display', () => {
  it('renders the two server amounts without combining them', () => {
    const html = renderToStaticMarkup(createElement(AmountBreakdown, { value: breakdown }));
    expectSeparated(html);
    expect(html).not.toContain('420만 원');
  });

  it('shows portfolio, policy and given-up breakdowns', () => {
    const portfolio = structuredClone(regular.results['1y'].pv_off.portfolios[0]);
    if (portfolio === undefined) throw new Error('fixture portfolio missing');
    portfolio.total_won = 4_200_000;
    portfolio.amount_breakdown = breakdown;
    portfolio.policies[0].amount_in_period_won = 4_200_000;
    portfolio.policies[0].amount_breakdown = breakdown;
    portfolio.given_up = [{ policy_id: 'youth:example:P6', amount_won: 4_200_000, amount_breakdown: breakdown }];

    const card = renderToStaticMarkup(createElement(PortfolioCard, {
      portfolio,
      topTotal: portfolio.total_won,
      selected: true,
      onSelect: () => undefined,
      info: new Map(),
    }));
    expectSeparated(card);
    expect(card).toContain('포기하는 지원금');

    const policy = renderToStaticMarkup(createElement(PolicyCard, {
      policy: portfolio.policies[0],
      info: new Map(),
      detailHref: '/policy/test',
      virtualData: true,
      aiGenerated: false,
    }));
    expectSeparated(policy);
  });

  it('shows the maturity fallback and its PV-off nominal breakdown', () => {
    const view = pickView(maturity, '1y', false);
    const html = renderToStaticMarkup(createElement(FallbackList, {
      items: view.fallback_list ?? [],
      info: new Map(),
    }));
    expectSeparated(html, '0만 원', '30만 원');
  });

  it('keeps the breakdown with the excluded policy opportunity cost', () => {
    const view = structuredClone(pickView(regular, '1y', false));
    const selected = view.portfolios[1];
    if (selected === undefined) throw new Error('fixture alternative missing');
    selected.given_up = [{
      policy_id: 'youth:example:P1',
      amount_won: 2_400_000,
      amount_breakdown: { cash_in_period_won: 0, expected_accrual_won: 2_400_000 },
    }];
    const html = renderToStaticMarkup(createElement(ResultLists, {
      view,
      selected,
      info: new Map(),
    }));
    expect(html).toContain('이 조합의 기회비용 240만 원');
    expectSeparated(html, '0만 원', '240만 원');
  });

  it('shows selected-period, annual, lost and gained breakdowns in the simulation screen', () => {
    const linked = structuredClone(simulation) as ChangeSimulationResult;
    linked.before_amount_breakdown = { cash_in_period_won: 1_200_000, expected_accrual_won: 1_200_000 };
    linked.after_amount_breakdown = { cash_in_period_won: 1_500_000, expected_accrual_won: 0 };
    linked.annual.before_amount_breakdown = { cash_in_period_won: 2_400_000, expected_accrual_won: 0 };
    linked.annual.after_amount_breakdown = { cash_in_period_won: 1_500_000, expected_accrual_won: 0 };
    if (linked.lost?.[0] !== undefined) {
      linked.lost[0].amount_breakdown = { cash_in_period_won: 0, expected_accrual_won: 2_400_000 };
    }
    if (linked.gained?.[0] !== undefined) {
      linked.gained[0].amount_breakdown = { cash_in_period_won: 1_500_000, expected_accrual_won: 0 };
    }

    const html = renderToStaticMarkup(createElement(SimulationResult, {
      result: linked,
      isSample: false,
      presentValue: true,
    }));
    expect(html).toContain('현재 연간 지원금');
    expect(html).toContain('바뀐 뒤 연간 지원금');
    expect(html).toContain('현재가치 합계와 순위에는 만기 전 예상 적립액을 넣지 않았어요.');
    expect(html.match(/현금 지급 예정/g)?.length).toBeGreaterThanOrEqual(6);
    expect(html.match(/만기 전 예상 적립/g)?.length).toBeGreaterThanOrEqual(6);
  });
});
