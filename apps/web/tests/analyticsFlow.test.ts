import { createElement } from 'react';
import type { EffectCallback } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AnalyticsEvent } from '../src/shared/analytics/events.ts';
import type { RegionOptionsState } from '../src/features/profile/regionOptions.ts';
import { isRecommendationResponse } from '../src/shared/api/recommendations.ts';
import fixtureText from '../../../contracts/fixtures/recommendation-response.json?raw';

type CheckboxHandler = (event: { target: { checked: boolean } }) => void;
type ClickHandler = () => void;

const analyticsFlow = vi.hoisted(() => ({
  effects: [] as EffectCallback[],
  events: [] as AnalyticsEvent[],
  trace: [] as string[],
  inputs: new Map<string, CheckboxHandler>(),
  buttons: [] as { label: string; onClick: ClickHandler; disabled: boolean }[],
  navigate: vi.fn(),
  regionState: { status: 'loading' } as RegionOptionsState,
}));

// 서버 렌더에서도 실제 JSX로 만든 input/button prop을 잡아 사용자 handler를 호출한다.
vi.mock('react/jsx-runtime', async (load) => {
  const actual = await load<typeof import('react/jsx-runtime')>();
  function capture(
    render: typeof actual.jsx,
  ): typeof actual.jsx {
    return (type, props, key) => {
      if (typeof type === 'string' && typeof props === 'object' && props !== null) {
        const record = props as Record<string, unknown>;
        if (
          type === 'input' &&
          (record.id === 's1-consent' || record.id === 's1-remember') &&
          typeof record.onChange === 'function'
        ) {
          analyticsFlow.inputs.set(record.id, record.onChange as CheckboxHandler);
        }
        if (type === 'button' && typeof record.children === 'string' && typeof record.onClick === 'function') {
          analyticsFlow.buttons.push({
            label: record.children,
            onClick: record.onClick as ClickHandler,
            disabled: record.disabled === true,
          });
        }
      }
      return render(type, props, key);
    };
  }
  return {
    ...actual,
    jsx: capture(actual.jsx),
    jsxs: capture(actual.jsxs),
  };
});

vi.mock('react/jsx-dev-runtime', async (load) => {
  const actual = await load<typeof import('react/jsx-dev-runtime')>();
  return {
    ...actual,
    jsxDEV(
      type: Parameters<typeof actual.jsxDEV>[0],
      props: unknown,
      key: Parameters<typeof actual.jsxDEV>[2],
      isStatic: boolean,
      source?: Parameters<typeof actual.jsxDEV>[4],
      self?: unknown,
    ) {
      if (typeof type === 'string' && typeof props === 'object' && props !== null) {
        const record = props as Record<string, unknown>;
        if (
          type === 'input' &&
          (record.id === 's1-consent' || record.id === 's1-remember') &&
          typeof record.onChange === 'function'
        ) {
          analyticsFlow.inputs.set(record.id, record.onChange as CheckboxHandler);
        }
        if (type === 'button' && typeof record.children === 'string' && typeof record.onClick === 'function') {
          analyticsFlow.buttons.push({
            label: record.children,
            onClick: record.onClick as ClickHandler,
            disabled: record.disabled === true,
          });
        }
      }
      return actual.jsxDEV(type, props, key, isStatic, source, self);
    },
  };
});

vi.mock('react', async (load) => ({
  ...await load<typeof import('react')>(),
  useSyncExternalStore: (_subscribe: unknown, getSnapshot: () => unknown) => getSnapshot(),
  useEffect: (effect: EffectCallback) => { analyticsFlow.effects.push(effect); },
}));

vi.mock('../src/shared/analytics/events.ts', async (load) => {
  const actual = await load<typeof import('../src/shared/analytics/events.ts')>();
  return {
    ...actual,
    trackEvent: (event: AnalyticsEvent) => {
      analyticsFlow.events.push(event);
      analyticsFlow.trace.push(`event:${event.name}`);
    },
    resetAnalyticsSession: () => {
      analyticsFlow.trace.push('reset');
    },
  };
});

vi.mock('../src/app/router.ts', async (load) => ({
  ...await load<typeof import('../src/app/router.ts')>(),
  navigate: analyticsFlow.navigate,
}));

vi.mock('../src/features/profile/regionOptions.ts', () => ({
  useRegionOptions: () => ({ state: analyticsFlow.regionState, retry: vi.fn() }),
}));

const REGIONS = [{ sido_code: '41', sido_name: '[가상] 경기도', sigungu: [{ code: '41110', name: '수원시' }] }];
const REQUEST = { domain: 'youth', profile: { age: 25 } } as const;
const FIXTURE: unknown = JSON.parse(fixtureText);
if (!isRecommendationResponse(FIXTURE)) { throw new Error('invalid synthetic recommendation fixture'); }
const DEFAULT_BASIS_FIXTURE = structuredClone(FIXTURE);
DEFAULT_BASIS_FIXTURE.results['1y'].pv_on.portfolios = [];
DEFAULT_BASIS_FIXTURE.results['2y'].pv_off.portfolios = [];
DEFAULT_BASIS_FIXTURE.results['2y'].pv_on.portfolios = [];
DEFAULT_BASIS_FIXTURE.results.all.pv_off.portfolios = [];
DEFAULT_BASIS_FIXTURE.results.all.pv_on.portfolios = [];
if (!isRecommendationResponse(DEFAULT_BASIS_FIXTURE)) { throw new Error('invalid default-basis fixture'); }

let monotonicNow = 0;

function storage(): Pick<Storage, 'getItem' | 'setItem' | 'removeItem'> {
  const values = new Map<string, string>();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => { values.set(key, value); },
    removeItem: (key) => { values.delete(key); },
  };
}

function response(result: unknown = FIXTURE, status = 200): Response {
  return new Response(JSON.stringify(result), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function button(label: string): { label: string; onClick: ClickHandler; disabled: boolean } {
  const found = analyticsFlow.buttons.find((entry) => entry.label === label);
  if (found === undefined) { throw new Error(`button not rendered: ${label}`); }
  return found;
}

beforeEach(() => {
  vi.resetModules();
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-11-14T00:00:00Z'));
  monotonicNow = 0;
  vi.stubGlobal('performance', { now: () => monotonicNow });
  analyticsFlow.effects = [];
  analyticsFlow.events = [];
  analyticsFlow.trace = [];
  analyticsFlow.inputs.clear();
  analyticsFlow.buttons = [];
  analyticsFlow.navigate.mockClear();
  analyticsFlow.regionState = { status: 'ready', regions: REGIONS };
  vi.stubGlobal('window', {
    sessionStorage: storage(),
    localStorage: storage(),
    location: { search: '' },
    setTimeout,
    clearTimeout,
  });
  vi.stubGlobal('fetch', vi.fn());
});

afterEach(() => {
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('S1 analytics transitions', () => {
  it('records mandatory consent and remember only on off-to-on changes', async () => {
    const { StartScreen } = await import('../src/features/profile/StartScreen.tsx');
    renderToStaticMarkup(createElement(StartScreen));
    const consent = analyticsFlow.inputs.get('s1-consent');
    const remember = analyticsFlow.inputs.get('s1-remember');
    if (consent === undefined || remember === undefined) { throw new Error('S1 checkboxes not rendered'); }

    consent({ target: { checked: true } });
    consent({ target: { checked: true } });
    consent({ target: { checked: false } });
    consent({ target: { checked: true } });
    remember({ target: { checked: true } });
    remember({ target: { checked: true } });
    remember({ target: { checked: false } });

    expect(analyticsFlow.events).toEqual([
      { name: 'consent_check' },
      { name: 'consent_check' },
      { name: 'remember_on' },
    ]);
  });

  it('does not emit from a restored checked state', async () => {
    const flow = await import('../src/features/profile/flowStore.ts');
    flow.updateFlowState({ consent: true, remember: true });
    const { StartScreen } = await import('../src/features/profile/StartScreen.tsx');
    renderToStaticMarkup(createElement(StartScreen));
    expect(analyticsFlow.events).toEqual([]);
  });

  it('records remember_on when reusing saved conditions turns it on', async () => {
    const flow = await import('../src/features/profile/flowStore.ts');
    flow.updateAnswers({
      ...flow.EMPTY_ANSWERS,
      age: 25,
      sidoCode: '41',
      regionCode: '41110',
      regionLabel: '[가상] 경기도 수원시',
    });
    const { SavedConditionsCard } = await import('../src/features/profile/SavedConditionsCard.tsx');
    renderToStaticMarkup(createElement(SavedConditionsCard, {
      saved: { savedAt: '2026-11-13T00:00:00Z', answers: flow.getFlowState().answers },
      onClear: vi.fn(),
    }));

    button('이 조건으로 다시 보기').onClick();
    button('이 조건으로 다시 보기').onClick();

    expect(analyticsFlow.events).toEqual([{ name: 'remember_on' }]);
    expect(flow.getFlowState()).toMatchObject({ consent: true, remember: true });
  });
});

describe('S2 analytics lifecycle', () => {
  it('records an authorized step view once across StrictMode effect replay', async () => {
    const flow = await import('../src/features/profile/flowStore.ts');
    flow.updateFlowState({ consent: true });
    const { ConditionScreen } = await import('../src/features/profile/ConditionScreen.tsx');
    renderToStaticMarkup(createElement(ConditionScreen, { step: 1 }));

    analyticsFlow.effects.forEach((effect) => { effect(); });
    analyticsFlow.effects.forEach((effect) => { effect(); });

    expect(analyticsFlow.events).toEqual([{ name: 'step_view', step: 1 }]);
    expect(analyticsFlow.navigate).not.toHaveBeenCalled();
  });

  it('does not record a step view before mandatory consent', async () => {
    const { ConditionScreen } = await import('../src/features/profile/ConditionScreen.tsx');
    renderToStaticMarkup(createElement(ConditionScreen, { step: 1 }));

    analyticsFlow.effects.forEach((effect) => { effect(); });

    expect(analyticsFlow.events).toEqual([]);
    expect(analyticsFlow.navigate).toHaveBeenCalledWith('/', { replace: true });
  });

  it.each(['다음', '내 조합 찾기'])('records enabled %s presses at the layout boundary', async (label) => {
    const { ConditionStepLayout } = await import('../src/features/profile/ConditionStepLayout.tsx');
    renderToStaticMarkup(createElement(ConditionStepLayout, {
      step: label === '내 조합 찾기' ? 13 : 1,
      title: '가상 질문',
      canNext: true,
      nextLabel: label,
      children: createElement('p', null, '가상 내용'),
    }));

    const next = button(label);
    expect(next.disabled).toBe(false);
    next.onClick();

    expect(analyticsFlow.events).toEqual([
      { name: 'step_complete', step: label === '내 조합 찾기' ? 13 : 1 },
    ]);
  });

  it('does not record a disabled next action', async () => {
    const { ConditionStepLayout } = await import('../src/features/profile/ConditionStepLayout.tsx');
    renderToStaticMarkup(createElement(ConditionStepLayout, {
      step: 1,
      title: '가상 질문',
      canNext: false,
      children: createElement('p', null, '가상 내용'),
    }));

    const next = button('다음');
    expect(next.disabled).toBe(true);
    next.onClick();
    expect(analyticsFlow.events).toEqual([]);
  });

  it('records step_skip only for the step 12 skip action', async () => {
    const flow = await import('../src/features/profile/flowStore.ts');
    flow.updateAnswers({ marital: 'single' });
    const { ParentsStep } = await import('../src/features/profile/steps/ParentsStep.tsx');
    renderToStaticMarkup(createElement(ParentsStep));

    button('건너뛰기').onClick();

    expect(analyticsFlow.events).toEqual([{ name: 'step_skip' }]);
  });
});

describe('analysis analytics lifecycle', () => {
  it('records one start and one success with actual elapsed time and the default-basis count', async () => {
    let finish: ((value: Response) => void) | undefined;
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((resolve) => { finish = resolve; })));
    const analysis = await import('../src/features/benefits/analysisStore.ts');

    analysis.startAnalysis(REQUEST);
    expect(analyticsFlow.events).toEqual([{ name: 'analyze_start' }]);

    monotonicNow = 1_234;
    if (finish === undefined) { throw new Error('request was not started'); }
    finish(response(DEFAULT_BASIS_FIXTURE));
    await vi.advanceTimersByTimeAsync(0);

    expect(analysis.getAnalysisState()).toMatchObject({ status: 'done', isSample: false });
    expect(analyticsFlow.events).toEqual([
      { name: 'analyze_start' },
      { name: 'analyze_success', duration_ms: 1_234, portfolio_count: 2 },
    ]);
  });

  it('records a fixed allowed failure code without the raw error', async () => {
    const secret = 'profile age=25 income=private';
    vi.stubGlobal('fetch', vi.fn(async () => response({ code: 'dependency_unavailable', message: secret }, 503)));
    const analysis = await import('../src/features/benefits/analysisStore.ts');

    analysis.startAnalysis(REQUEST);
    await vi.advanceTimersByTimeAsync(0);

    expect(analysis.getAnalysisState()).toEqual({ status: 'failed', reason: 'error' });
    expect(analyticsFlow.events).toEqual([
      { name: 'analyze_start' },
      { name: 'analyze_fail', code: 'dependency_unavailable' },
    ]);
    expect(JSON.stringify(analyticsFlow.events)).not.toContain(secret);
  });

  it('records one timeout failure and ignores a late result', async () => {
    let finish: ((value: Response) => void) | undefined;
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((resolve) => { finish = resolve; })));
    const analysis = await import('../src/features/benefits/analysisStore.ts');

    analysis.startAnalysis(REQUEST);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(analysis.getAnalysisState()).toEqual({ status: 'failed', reason: 'error' });
    expect(analyticsFlow.events).toEqual([
      { name: 'analyze_start' },
      { name: 'analyze_fail', code: 'timeout' },
    ]);

    if (finish === undefined) { throw new Error('request was not started'); }
    finish(response());
    await vi.advanceTimersByTimeAsync(0);
    expect(analyticsFlow.events).toHaveLength(2);
    expect(analysis.getAnalysisState()).toEqual({ status: 'failed', reason: 'error' });
  });

  it('does not record fail for user cancellation or success for a stale response', async () => {
    const pending: { resolve: (value: Response) => void; signal?: AbortSignal | null }[] = [];
    vi.stubGlobal('fetch', vi.fn((_input: RequestInfo | URL, init?: RequestInit) => new Promise<Response>((resolve) => {
      pending.push({ resolve, signal: init?.signal });
    })));
    const analysis = await import('../src/features/benefits/analysisStore.ts');

    analysis.startAnalysis(REQUEST);
    analysis.cancelAnalysis();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(pending[0]?.signal?.aborted).toBe(true);
    expect(analyticsFlow.events).toEqual([{ name: 'analyze_start' }]);

    analysis.startAnalysis(REQUEST);
    analysis.startAnalysis(REQUEST);
    pending[1]?.resolve(response());
    await vi.advanceTimersByTimeAsync(0);
    expect(analyticsFlow.events.filter((event) => event.name === 'analyze_success')).toEqual([]);

    pending[2]?.resolve(response());
    await vi.advanceTimersByTimeAsync(0);
    expect(analyticsFlow.events.filter((event) => event.name === 'analyze_success')).toHaveLength(1);
    expect(analyticsFlow.events.filter((event) => event.name === 'analyze_fail')).toEqual([]);
  });
});

describe('clearing local data', () => {
  it('records clear_mine and resets analytics once for each clear action', async () => {
    const { clearMyData } = await import('../src/features/profile/clearMyData.ts');

    clearMyData();
    clearMyData();

    expect(analyticsFlow.trace).toEqual([
      'event:clear_mine', 'reset',
      'event:clear_mine', 'reset',
    ]);
  });
});
