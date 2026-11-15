import { isValidElement } from 'react';
import type { EffectCallback, ReactElement, ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { NormalizedBenefit } from '../src/shared/api/benefits.ts';
import { isNormalizedBenefit } from '../src/shared/api/benefits.ts';
import type { RecommendationResponse } from '../src/shared/api/recommendations.ts';
import { isRecommendationResponse } from '../src/shared/api/recommendations.ts';
import type { PortfolioPolicy, ResultView } from '../src/features/benefits/resultModel.ts';
import recommendationText from '../../../contracts/fixtures/recommendation-response.json?raw';
import benefitText from '../../../contracts/fixtures/youth-benefit.json?raw';

const analytics = vi.hoisted(() => ({ trackEvent: vi.fn() }));
const router = vi.hoisted(() => ({ navigate: vi.fn() }));
const image = vi.hoisted(() => ({ saveResultImage: vi.fn(), showToast: vi.fn() }));
const simulation = vi.hoisted(() => ({ retry: vi.fn() }));
const tree = vi.hoisted(() => ({
  effects: [] as EffectCallback[],
  stateValues: [] as unknown[],
  stateIndex: 0,
  updates: [] as { index: number; value: unknown }[],
  refs: [] as { current: unknown }[],
  result: null as unknown,
  analysisStatus: 'idle' as 'idle' | 'done',
  consent: true,
  detailState: { status: 'loading' } as Record<string, unknown>,
}));

vi.mock('../src/shared/analytics/events.ts', () => ({ trackEvent: analytics.trackEvent }));
vi.mock('../src/app/router.ts', async (load) => {
  const actual = await load<typeof import('../src/app/router.ts')>();
  return { ...actual, navigate: router.navigate };
});
vi.mock('react', async (load) => {
  const actual = await load<typeof import('react')>();
  return {
    ...actual,
    useEffect: (effect: EffectCallback) => { tree.effects.push(effect); },
    useRef: <T,>(initial: T) => {
      const ref = { current: initial };
      tree.refs.push(ref as { current: unknown });
      return ref;
    },
    useState: <T,>(initial: T) => {
      const index = tree.stateIndex++;
      const value = (tree.stateValues[index] ?? initial) as T;
      return [value, (nextValue: T | ((previous: T) => T)) => {
        const resolved = typeof nextValue === 'function'
          ? (nextValue as (previous: T) => T)(value)
          : nextValue;
        tree.updates.push({ index, value: resolved });
      }] as const;
    },
  };
});
vi.mock('../src/features/benefits/analysisStore.ts', () => ({
  useAnalysis: () => tree.analysisStatus === 'done'
    ? { status: 'done', result: tree.result, isSample: false }
    : { status: 'idle' },
}));
vi.mock('../src/features/profile/flowStore.ts', () => ({
  useFlowState: () => ({ answers: {}, consent: tree.consent }),
}));
vi.mock('../src/features/profile/answerRules.ts', () => ({ missingSteps: () => [] }));
vi.mock('../src/features/profile/toRequest.ts', () => ({
  toRecommendationRequest: () => ({ profile: {} }),
}));
vi.mock('../src/features/benefits/policyInfo.ts', () => ({ usePolicyInfo: () => new Map() }));
vi.mock('../src/features/policy/usePolicyDetail.ts', () => ({
  usePolicyDetail: () => ({ state: tree.detailState, retry: vi.fn() }),
}));
vi.mock('../src/features/whatIf/useSimulation.ts', () => ({
  useSimulation: () => ({ state: { status: 'loading' }, retry: simulation.retry }),
}));
vi.mock('../src/features/benefits/resultImage.ts', () => ({ saveResultImage: image.saveResultImage }));
vi.mock('../src/shared/ui/toastStore.ts', () => ({ showToast: image.showToast }));

import { Button } from '../src/shared/ui/Button.tsx';
import { ChoiceChip } from '../src/shared/ui/ChoiceChip.tsx';
import { FallbackList } from '../src/features/benefits/FallbackList.tsx';
import { PolicyCard } from '../src/features/benefits/PolicyCard.tsx';
import { PortfolioCard } from '../src/features/benefits/PortfolioCard.tsx';
import { ResultScreen } from '../src/features/benefits/ResultScreen.tsx';
import { PolicyDetailScreen } from '../src/features/policy/PolicyDetailScreen.tsx';
import { WhatIfScreen } from '../src/features/whatIf/WhatIfScreen.tsx';

type FallbackItem = NonNullable<ResultView['fallback_list']>[number];

function parseRecommendation(raw: string): RecommendationResponse {
  const parsed: unknown = JSON.parse(raw);
  if (!isRecommendationResponse(parsed)) {
    throw new Error('invalid synthetic recommendation fixture');
  }
  return parsed;
}

function parseBenefit(raw: string): NormalizedBenefit {
  const parsed: unknown = JSON.parse(raw);
  if (!isNormalizedBenefit(parsed)) {
    throw new Error('invalid synthetic benefit fixture');
  }
  return parsed;
}

const RECOMMENDATION = parseRecommendation(recommendationText);
const BENEFIT = parseBenefit(benefitText);

function benefitFor(
  id: string,
  status: NormalizedBenefit['application']['status'] = 'open',
  virtualData = false,
): NormalizedBenefit {
  return {
    ...BENEFIT,
    id,
    virtual_data: virtualData,
    application: {
      status,
      starts_on: null,
      ends_on: null,
      url: status === 'open' ? 'https://policy.example/youth/apply' : null,
    },
  };
}

function firstPolicy(): PortfolioPolicy {
  const policy = RECOMMENDATION.results['1y'].pv_off.portfolios[0]?.policies[0];
  if (policy === undefined) {
    throw new Error('recommendation fixture has no portfolio policy');
  }
  return policy;
}

function resetHooks(values: readonly unknown[] = []): void {
  tree.effects = [];
  tree.stateValues = [...values];
  tree.stateIndex = 0;
  tree.updates = [];
  tree.refs = [];
}

function propsOf(element: ReactElement): Record<string, unknown> {
  return element.props as Record<string, unknown>;
}

function allElements(node: ReactNode): ReactElement[] {
  const found: ReactElement[] = [];
  function walk(child: ReactNode): void {
    if (Array.isArray(child)) {
      child.forEach(walk);
      return;
    }
    if (!isValidElement(child)) {
      return;
    }
    found.push(child);
    walk((child.props as { children?: ReactNode }).children);
  }
  walk(node);
  return found;
}

function handler(element: ReactElement, name: string): (...args: unknown[]) => unknown {
  const value = propsOf(element)[name];
  if (typeof value !== 'function') {
    throw new Error('missing event handler ' + name);
  }
  return value as (...args: unknown[]) => unknown;
}

function tabs(node: ReactNode): ReactElement[] {
  return allElements(node).filter((element) =>
    element.type === 'button' && propsOf(element).role === 'tab');
}

function applyLink(node: ReactNode, classNamePart: string): ReactElement | undefined {
  return allElements(node).find((element) =>
    element.type === 'a' &&
    typeof propsOf(element).className === 'string' &&
    String(propsOf(element).className).includes(classNamePart) &&
    propsOf(element).target === '_blank');
}

function renderResult(
  result: RecommendationResponse = RECOMMENDATION,
  states: readonly unknown[] = ['1y', false, 1],
): ReactNode {
  tree.analysisStatus = 'done';
  tree.result = result;
  resetHooks(states);
  return ResultScreen();
}

async function clickImageSave(node: ReactNode): Promise<void> {
  const save = allElements(node).find((element) =>
    element.type === Button && propsOf(element).variant === 'secondary');
  if (save === undefined) {
    throw new Error('missing image save button');
  }
  handler(save, 'onClick')();
  await Promise.resolve();
  await Promise.resolve();
}

function renderPolicyCard(benefit: NormalizedBenefit, virtualData = false): ReactNode {
  const policy = firstPolicy();
  return PolicyCard({
    policy,
    rank: 2,
    info: new Map([[policy.policy_id, benefit]]),
    aiGenerated: false,
    detailHref: '/policy/' + policy.policy_id,
    virtualData,
  });
}

function renderFallback(benefit: NormalizedBenefit, virtualData = false): ReactNode {
  const item = {
    policy_id: benefit.id,
    amount_in_period_won: null,
  } as FallbackItem;
  return FallbackList({
    items: [item],
    info: new Map([[benefit.id, benefit]]),
    virtualData,
  });
}

function renderDetail(benefit: NormalizedBenefit): ReactNode {
  tree.detailState = { status: 'ok', benefit, isSample: false };
  tree.analysisStatus = 'idle';
  resetHooks();
  return PolicyDetailScreen({ policyId: benefit.id });
}

function expandedDetail(node: ReactNode, benefit: NormalizedBenefit): ReactNode {
  const element = allElements(node).find((candidate) => {
    const props = propsOf(candidate);
    return typeof candidate.type === 'function' &&
      typeof props.benefit === 'object' && props.benefit !== null &&
      (props.benefit as NormalizedBenefit).id === benefit.id;
  });
  if (element === undefined || typeof element.type !== 'function') {
    throw new Error('missing PolicyDetail component');
  }
  return (element.type as (props: Record<string, unknown>) => ReactNode)(propsOf(element));
}

function renderWhatIf(
  scenario: 'employed' | 'income_up' | 'independent' | 'age_plus_one' = 'employed',
  authorized = true,
): ReactNode {
  tree.analysisStatus = authorized ? 'done' : 'idle';
  tree.result = RECOMMENDATION;
  resetHooks([scenario]);
  return WhatIfScreen();
}

beforeEach(() => {
  analytics.trackEvent.mockReset();
  router.navigate.mockReset();
  image.saveResultImage.mockReset();
  image.showToast.mockReset();
  simulation.retry.mockReset();
  tree.result = null;
  tree.analysisStatus = 'idle';
  tree.consent = true;
  tree.detailState = { status: 'loading' };
  resetHooks();
  vi.stubGlobal('window', { location: { search: '' }, history: { length: 2 } });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('result control handlers', () => {
  it('records a period only when the user changes tabs and keeps the existing navigation', () => {
    const controls = tabs(renderResult());
    expect(controls).toHaveLength(3);

    handler(controls[1] as ReactElement, 'onClick')();
    expect(analytics.trackEvent).toHaveBeenCalledWith({ name: 'period_change', period: '2y' });
    expect(tree.updates).toContainEqual({ index: 0, value: '2y' });
    expect(tree.updates).toContainEqual({ index: 2, value: 1 });
    expect(router.navigate).toHaveBeenCalledWith('/result?period=2y', { replace: true });

    handler(controls[0] as ReactElement, 'onClick')();
    expect(analytics.trackEvent).toHaveBeenCalledTimes(1);
    expect(router.navigate).toHaveBeenCalledTimes(2);
    expect(tree.updates).toContainEqual({ index: 0, value: '1y' });
  });

  it('records present-value changes only when the checkbox value changes', () => {
    let node = renderResult();
    let checkbox = allElements(node).find((element) =>
      element.type === 'input' && propsOf(element).id === 's3-pv');
    if (checkbox === undefined) {
      throw new Error('missing PV checkbox');
    }
    handler(checkbox, 'onChange')({ target: { checked: true } });
    expect(analytics.trackEvent).toHaveBeenCalledWith({ name: 'pv_toggle', on: true });
    expect(tree.updates).toContainEqual({ index: 1, value: true });

    analytics.trackEvent.mockClear();
    node = renderResult(RECOMMENDATION, ['1y', true, 1]);
    checkbox = allElements(node).find((element) =>
      element.type === 'input' && propsOf(element).id === 's3-pv');
    if (checkbox === undefined) {
      throw new Error('missing PV checkbox');
    }
    handler(checkbox, 'onChange')({ target: { checked: false } });
    expect(analytics.trackEvent).toHaveBeenCalledWith({ name: 'pv_toggle', on: false });

    handler(checkbox, 'onChange')({ target: { checked: true } });
    expect(analytics.trackEvent).toHaveBeenCalledTimes(1);
    expect(tree.updates).toContainEqual({ index: 1, value: true });
    expect(router.navigate).toHaveBeenCalledTimes(3);
  });

  it('records a combination card only when the selected rank changes', () => {
    const node = renderResult();
    const cards = allElements(node).filter((element) => element.type === PortfolioCard);
    expect(cards.length).toBeGreaterThan(1);

    handler(cards[1] as ReactElement, 'onSelect')();
    expect(analytics.trackEvent).toHaveBeenCalledWith({ name: 'combo_select', rank: 2 });
    expect(tree.updates).toContainEqual({ index: 2, value: 2 });

    handler(cards[0] as ReactElement, 'onSelect')();
    expect(analytics.trackEvent).toHaveBeenCalledTimes(1);
    expect(tree.updates).toContainEqual({ index: 2, value: 1 });
  });

  it('passes the selected portfolio rank to each result policy card', () => {
    const node = renderResult();
    const cards = allElements(node).filter((element) => element.type === PolicyCard);
    expect(cards.length).toBeGreaterThan(0);
    expect(cards.every((card) => propsOf(card).rank === 1)).toBe(true);
  });

  it('records image_save only when saveResultImage resolves true', async () => {
    image.saveResultImage.mockResolvedValueOnce(true);
    await clickImageSave(renderResult());
    expect(analytics.trackEvent).toHaveBeenCalledWith({ name: 'image_save' });
    expect(image.showToast).toHaveBeenCalledTimes(1);

    analytics.trackEvent.mockClear();
    image.showToast.mockClear();
    image.saveResultImage.mockResolvedValueOnce(false);
    await clickImageSave(renderResult());
    expect(analytics.trackEvent).not.toHaveBeenCalled();
    expect(image.showToast).not.toHaveBeenCalled();
  });
});

describe('application-link handlers', () => {
  it('records a selected-result application click with the propagated rank', () => {
    const policy = firstPolicy();
    const link = applyLink(renderPolicyCard(benefitFor(policy.policy_id)), 'ui-button--primary');
    if (link === undefined) {
      throw new Error('missing application link');
    }
    handler(link, 'onClick')();

    expect(analytics.trackEvent).toHaveBeenCalledWith({
      name: 'apply_click',
      policy_id: policy.policy_id,
      from: 'result',
      rank: 2,
    });
  });

  it('leaves closed source-only result links without an analytics handler', () => {
    const policy = firstPolicy();
    const link = applyLink(renderPolicyCard(benefitFor(policy.policy_id, 'closed')), 'ui-button--primary');
    if (link === undefined) {
      throw new Error('missing source fallback link');
    }
    expect(propsOf(link).onClick).toBeUndefined();
  });

  it('records fallback application clicks with rank null and guards sample/virtual links', () => {
    const policy = firstPolicy();
    const link = applyLink(renderFallback(benefitFor(policy.policy_id)), 'ui-button--primary');
    if (link === undefined) {
      throw new Error('missing fallback application link');
    }
    handler(link, 'onClick')();
    expect(analytics.trackEvent).toHaveBeenCalledWith({
      name: 'apply_click',
      policy_id: policy.policy_id,
      from: 'result',
      rank: null,
    });

    analytics.trackEvent.mockClear();
    const virtualLink = applyLink(renderFallback(benefitFor(policy.policy_id), true), 'ui-button--primary');
    if (virtualLink === undefined) {
      throw new Error('missing virtual fallback link');
    }
    expect(propsOf(virtualLink).onClick).toBeUndefined();
    expect(analytics.trackEvent).not.toHaveBeenCalled();
  });

  it('records detail application clicks with rank null and only for the open application URL', () => {
    const benefit = benefitFor('youth:example:housing-001');
    const link = applyLink(expandedDetail(renderDetail(benefit), benefit), 'ui-button--large');
    if (link === undefined) {
      throw new Error('missing detail application link');
    }
    handler(link, 'onClick')();
    expect(analytics.trackEvent).toHaveBeenCalledWith({
      name: 'apply_click',
      policy_id: benefit.id,
      from: 'detail',
      rank: null,
    });

    analytics.trackEvent.mockClear();
    const sourceOnly = benefitFor('youth:example:housing-001', 'unknown');
    const sourceLink = applyLink(expandedDetail(renderDetail(sourceOnly), sourceOnly), 'ui-button--large');
    if (sourceLink === undefined) {
      throw new Error('missing official source link');
    }
    expect(propsOf(sourceLink).onClick).toBeUndefined();
    expect(analytics.trackEvent).not.toHaveBeenCalled();
  });
});

describe('screen-open effects and what-if handlers', () => {
  it('records detail_view once for repeated StrictMode effect runs', () => {
    vi.stubGlobal('window', { location: { search: '' }, history: { length: 2 } });
    const benefit = benefitFor('youth:example:housing-001');
    renderDetail(benefit);
    const effects = [...tree.effects];
    effects.forEach((effect) => effect());
    effects.forEach((effect) => effect());

    expect(analytics.trackEvent).toHaveBeenCalledTimes(1);
    expect(analytics.trackEvent).toHaveBeenCalledWith({
      name: 'detail_view',
      policy_id: benefit.id,
    });
  });

  it('records whatif_open once only when S4 has an authorized analysis display', () => {
    renderWhatIf('employed', true);
    const effects = [...tree.effects];
    effects.forEach((effect) => effect());
    effects.forEach((effect) => effect());
    expect(analytics.trackEvent).toHaveBeenCalledTimes(1);
    expect(analytics.trackEvent).toHaveBeenCalledWith({ name: 'whatif_open' });

    analytics.trackEvent.mockClear();
    renderWhatIf('employed', false);
    tree.effects.forEach((effect) => effect());
    expect(analytics.trackEvent).not.toHaveBeenCalled();
  });

  it('records an actual scenario chip change and keeps the existing reset/retry flow', () => {
    const node = renderWhatIf();
    const chips = allElements(node).filter((element) => element.type === ChoiceChip);
    expect(chips).toHaveLength(4);

    handler(chips[1] as ReactElement, 'onSelect')();
    expect(analytics.trackEvent).toHaveBeenCalledWith({
      name: 'whatif_select',
      scenario: 'income_up',
    });
    expect(tree.updates).toContainEqual({ index: 0, value: 'income_up' });
    expect(simulation.retry).toHaveBeenCalledTimes(1);

    handler(chips[0] as ReactElement, 'onSelect')();
    expect(analytics.trackEvent).toHaveBeenCalledTimes(1);
    expect(simulation.retry).toHaveBeenCalledTimes(1);
  });
});
