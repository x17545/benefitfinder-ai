import { createElement } from 'react';
import type { EffectCallback } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Period } from '../src/features/benefits/format.ts';
import type { RecommendationResponse } from '../src/shared/api/recommendations.ts';
import { isRecommendationResponse } from '../src/shared/api/recommendations.ts';
import fixtureText from '../../../contracts/fixtures/recommendation-response.json?raw';
import emptyText from '../../../contracts/fixtures/empty-response.json?raw';
import fallbackText from '../../../contracts/fixtures/fallback-response.json?raw';
import unavailableText from '../../../contracts/fixtures/pv-unknown-response.json?raw';

const screen = vi.hoisted(() => ({
  result: null as RecommendationResponse | null,
  period: '1y' as Period,
  presentValue: false,
  effects: [] as EffectCallback[],
}));

// DOM 의존성 없이 실제 화면을 렌더한다. 기간/PV 선택 상태와 effect 실행 시점만 지정한다.
vi.mock('react', async (load) => {
  const actual = await load<typeof import('react')>();
  return {
    ...actual,
    useSyncExternalStore: (_subscribe: unknown, getSnapshot: () => unknown) => getSnapshot(),
    useEffect: (effect: EffectCallback) => { screen.effects.push(effect); },
    useState: (initial: unknown) => actual.useState(
      initial === '1y' ? screen.period : initial === false ? screen.presentValue : initial,
    ),
  };
});
vi.mock('../src/features/benefits/analysisStore.ts', async (load) => ({
  ...await load<typeof import('../src/features/benefits/analysisStore.ts')>(),
  useAnalysis: () => screen.result === null ? { status: 'idle' } : { status: 'done', result: screen.result, isSample: false },
}));
vi.mock('../src/features/benefits/policyInfo.ts', () => ({ usePolicyInfo: () => new Map() }));
vi.mock('../src/features/profile/regionOptions.ts', () => ({
  useRegionOptions: () => ({ state: { status: 'loading' }, retry: vi.fn() }),
}));

function storage(): Pick<Storage, 'getItem' | 'setItem' | 'removeItem'> {
  const values = new Map<string, string>();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => { values.set(key, value); },
    removeItem: (key) => { values.delete(key); },
  };
}

function fixture(text: string): RecommendationResponse {
  const result: unknown = JSON.parse(text);
  if (!isRecommendationResponse(result)) { throw new Error('invalid synthetic fixture'); }
  return result;
}

beforeEach(() => {
  vi.resetModules();
  vi.stubGlobal('window', { sessionStorage: storage(), localStorage: storage() });
  screen.effects = [];
  screen.period = '1y';
  screen.presentValue = false;
  screen.result = null;
});
afterEach(() => vi.unstubAllGlobals());

async function renderResult(): Promise<string> {
  const { ResultScreen } = await import('../src/features/benefits/ResultScreen.tsx');
  return renderToStaticMarkup(createElement(ResultScreen));
}

describe('remember choice on S1', () => {
  it.each([true, false])('keeps remember=%s on return and refresh even when saved conditions exist', async (consent) => {
    const flow = await import('../src/features/profile/flowStore.ts');
    const { saveConditions } = await import('../src/features/profile/savedConditions.ts');
    saveConditions({ age: 25 });
    flow.updateFlowState({ consent, remember: true });
    flow.updateFlowState({ remember: false });

    for (let visit = 0; visit < 2; visit += 1) {
      const { StartScreen } = await import('../src/features/profile/StartScreen.tsx');
      const html = renderToStaticMarkup(createElement(StartScreen));
      screen.effects.forEach((effect) => effect());
      screen.effects = [];
      expect(html).toContain('지난번 조건으로 바로 보기');
      expect(html.match(/<input[^>]*id="s1-remember"[^>]*>/)?.[0]).not.toContain('checked');
      expect((await import('../src/features/profile/flowStore.ts')).getFlowState().remember).toBe(false);
      vi.resetModules(); // 다음 렌더는 새로고침 뒤의 저장값에서 시작한다.
    }
  });

  it('keeps an explicit enabled choice on refresh', async () => {
    const flow = await import('../src/features/profile/flowStore.ts');
    flow.updateFlowState({ consent: true, remember: true });
    vi.resetModules();
    const { StartScreen } = await import('../src/features/profile/StartScreen.tsx');
    const html = renderToStaticMarkup(createElement(StartScreen));
    expect(html.match(/<input[^>]*id="s1-remember"[^>]*>/)?.[0]).toContain('checked');
  });
});

describe('period-specific empty results', () => {
  it('keeps tabs when 1y is empty and shows the 2y portfolio when selected', async () => {
    const result = fixture(fixtureText);
    result.view = 'empty';
    result.results['1y'] = fixture(emptyText).results['1y'];
    screen.result = result;
    const initial = await renderResult();
    expect(initial).toContain('role="tablist"');
    expect(initial).toContain('지금 다루는 정책 중엔 맞는 게 없어요');
    expect(initial).not.toContain('지원금 0개를 찾았어요');

    screen.period = '2y';
    const later = await renderResult();
    expect(later).toContain('aria-label="추천 조합 요약"');
    expect(later).toContain('600만 원');
    expect(later).not.toContain('지금 다루는 정책 중엔 맞는 게 없어요');
  });

  it('keeps tabs for a later fallback list and uses that period amount', async () => {
    const result = fixture(fallbackText);
    result.view = 'empty';
    result.results['1y'] = fixture(emptyText).results['1y'];
    screen.result = result;
    expect(await renderResult()).toContain('role="tablist"');
    screen.period = '2y';
    const html = await renderResult();
    expect(html).toContain('300만 원');
    expect(html).not.toContain('150만 원');
    expect(html).not.toContain('조합 비교');
  });

  it('shows a selected empty period without hiding the other tabs', async () => {
    const result = fixture(fixtureText);
    result.results['2y'] = fixture(emptyText).results['2y'];
    screen.result = result;
    screen.period = '2y';
    const html = await renderResult();
    expect(html).toContain('role="tablist"');
    expect(html).toContain('지금 다루는 정책 중엔 맞는 게 없어요');
    expect(html).not.toContain('aria-label="추천 조합 요약"');
  });

  it('distinguishes an unavailable PV calculation from an empty result', async () => {
    screen.result = fixture(unavailableText);
    screen.presentValue = true;
    const html = await renderResult();
    expect(html).toContain('role="tablist"');
    expect(html).toContain('이 기준으로는 계산할 수 없어요');
    expect(html).not.toContain('지금 다루는 정책 중엔 맞는 게 없어요');
  });

  it('keeps the complete empty screen when all six branches are empty', async () => {
    screen.result = fixture(emptyText);
    const html = await renderResult();
    expect(html).toContain('지금 다루는 정책 중엔 맞는 게 없어요');
    expect(html).toContain('온통청년에서 더 찾아보기');
    expect(html).not.toContain('role="tablist"');
  });
});
