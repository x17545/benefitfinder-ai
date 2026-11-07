import { createElement } from 'react';
import type { EffectCallback } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ConditionAnswers } from '../src/features/profile/flowStore.ts';
import fixtureText from '../../../contracts/fixtures/recommendation-response.json?raw';

const screen = vi.hoisted(() => ({
  effects: [] as EffectCallback[],
  navigate: vi.fn(),
}));

// DOM 없이 실제 저장소의 스냅샷을 읽고 effect 실행·정리 시점만 제어한다.
vi.mock('react', async (load) => ({
  ...await load<typeof import('react')>(),
  useSyncExternalStore: (_subscribe: unknown, getSnapshot: () => unknown) => getSnapshot(),
  useEffect: (effect: EffectCallback) => { screen.effects.push(effect); },
}));
vi.mock('../src/app/router.ts', async (load) => ({
  ...await load<typeof import('../src/app/router.ts')>(),
  navigate: screen.navigate,
}));
// 지역 조회는 별도 경계다. 결합 후에도 이 테스트는 분석 POST의 수명주기를 확인한다.
vi.mock('../src/features/profile/regionOptions.ts', () => ({
  useRegionOptions: () => ({
    state: { status: 'ready', regions: [{
      sido_code: '41', sido_name: '[가상] 경기도',
      sigungu: [{ code: '41110', name: '수원시' }],
    }] },
    retry: vi.fn(),
  }),
}));

const RESULT_KEY = 'ypm.lastResult';
const RESPONSE_TYPES = ['application/json', 'text/event-stream'] as const;

function storage(): Pick<Storage, 'getItem' | 'setItem' | 'removeItem'> {
  const values = new Map<string, string>();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => { values.set(key, value); },
    removeItem: (key) => { values.delete(key); },
  };
}

function response(type: typeof RESPONSE_TYPES[number]): Response {
  const body = type === 'application/json' ? fixtureText : `event: result\ndata: ${fixtureText.replace(/\n/g, '')}\n\n`;
  return new Response(body, { headers: { 'Content-Type': type } });
}

function pendingFetch(): {
  fetchFn: ReturnType<typeof vi.fn<typeof fetch>>;
  finish: (result: Response) => void;
} {
  let resolve: ((result: Response) => void) | undefined;
  // 취소 신호를 무시하고 응답해도 늦은 결과가 저장되지 않는지 확인한다.
  const fetchFn = vi.fn<typeof fetch>(() => new Promise<Response>((done) => { resolve = done; }));
  vi.stubGlobal('fetch', fetchFn);
  return {
    fetchFn,
    finish: (result) => {
      if (resolve === undefined) { throw new Error('request was not started'); }
      resolve(result);
    },
  };
}

let session: ReturnType<typeof storage>;
let analysis: typeof import('../src/features/benefits/analysisStore.ts') | undefined;
const cleanups: (() => void)[] = [];

beforeEach(() => {
  vi.resetModules();
  vi.useFakeTimers();
  screen.effects = [];
  screen.navigate.mockClear();
  session = storage();
  analysis = undefined;
  vi.stubGlobal('window', { sessionStorage: session, localStorage: storage(), setTimeout, clearTimeout });
});

afterEach(() => {
  cleanups.splice(0).forEach((cleanup) => { cleanup(); });
  analysis?.clearAnalysis();
  vi.clearAllTimers();
  vi.restoreAllMocks();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

async function prepareFlow(patch: Partial<ConditionAnswers> = {}, consent = true): Promise<void> {
  const flow = await import('../src/features/profile/flowStore.ts');
  flow.updateFlowState({ consent, remember: false });
  flow.updateAnswers({
    ...flow.EMPTY_ANSWERS,
    age: 25,
    sidoCode: '41',
    regionCode: '41110',
    regionLabel: '[가상] 경기도 수원시',
    employmentStatus: 'job_seeker',
    school: 'university_graduate',
    marital: 'single',
    living: 'alone',
    homeOwnership: 'homeless',
    householdHead: 'head',
    contractType: 'monthly_rent',
    depositBand: 'under_50m',
    rentBand: 'between_40_60',
    incomeBand: 'none',
    assetBand: 'under_122m',
    special: ['none'],
    interests: ['employment', 'housing'],
    received: ['none'],
    parentsSkipped: true,
    ...patch,
  });
  analysis = await import('../src/features/benefits/analysisStore.ts');
}

async function captureEffects(): Promise<EffectCallback[]> {
  screen.effects = [];
  const { LoadingScreen } = await import('../src/features/benefits/LoadingScreen.tsx');
  renderToStaticMarkup(createElement(LoadingScreen));
  return [...screen.effects];
}

function setup(effects: readonly EffectCallback[]): () => void {
  const pending = effects.map((effect) => effect());
  const cleanup = (): void => {
    pending.splice(0).forEach((dispose) => {
      if (typeof dispose === 'function') { dispose(); }
    });
  };
  cleanups.push(cleanup);
  return cleanup;
}

describe('loading screen lifecycle', () => {
  it.each(RESPONSE_TYPES)('starts one POST after setup-cleanup-setup and accepts %s', async (type) => {
    await prepareFlow();
    const request = pendingFetch();
    const effects = await captureEffects();

    setup(effects)();
    setup(effects);
    expect(request.fetchFn).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(0);
    expect(request.fetchFn).toHaveBeenCalledExactlyOnceWith('/api/v1/recommendations', expect.objectContaining({
      method: 'POST',
      signal: expect.any(AbortSignal),
    }));
    expect(analysis?.getAnalysisState()).toEqual({ status: 'running', progress: [] });
    expect(request.fetchFn.mock.calls[0]?.[1]?.signal?.aborted).toBe(false);

    request.finish(response(type));
    await vi.advanceTimersByTimeAsync(0);
    expect(analysis?.getAnalysisState()).toMatchObject({ status: 'done', isSample: false });
    expect(session.getItem(RESULT_KEY)).not.toBeNull();
    expect(request.fetchFn).toHaveBeenCalledTimes(1);
  });

  it('does not send a POST when unmounted before the scheduled start', async () => {
    await prepareFlow();
    const request = pendingFetch();
    setup(await captureEffects())();

    await vi.advanceTimersByTimeAsync(0);
    expect(request.fetchFn).not.toHaveBeenCalled();
    expect(analysis?.getAnalysisState()).toEqual({ status: 'idle' });
    expect(screen.navigate).not.toHaveBeenCalled();
  });

  it.each(RESPONSE_TYPES)('aborts on unmount and ignores a late %s result', async (type) => {
    await prepareFlow();
    const request = pendingFetch();
    const unmount = setup(await captureEffects());
    await vi.advanceTimersByTimeAsync(0);
    expect(request.fetchFn).toHaveBeenCalledTimes(1);
    const signal = request.fetchFn.mock.calls[0]?.[1]?.signal;
    expect(signal?.aborted).toBe(false);
    expect(analysis?.getAnalysisState().status).toBe('running');

    unmount();
    expect(signal?.aborted).toBe(true);
    expect(analysis?.getAnalysisState()).toEqual({ status: 'idle' });
    request.finish(response(type));
    await vi.advanceTimersByTimeAsync(0);
    expect(analysis?.getAnalysisState()).toEqual({ status: 'idle' });
    expect(session.getItem(RESULT_KEY)).toBeNull();
    expect(screen.navigate).not.toHaveBeenCalled();
    expect(request.fetchFn).toHaveBeenCalledTimes(1);
  });

  it('clears a previous done cache before starting and never navigates with the stale snapshot', async () => {
    await prepareFlow();
    const result: unknown = JSON.parse(fixtureText);
    session.setItem(RESULT_KEY, JSON.stringify({ result, isSample: false }));
    vi.resetModules();
    analysis = await import('../src/features/benefits/analysisStore.ts');
    expect(analysis.getAnalysisState().status).toBe('done');
    expect(session.getItem(RESULT_KEY)).not.toBeNull();
    const request = pendingFetch();
    // 첫 렌더의 두 effect 모두 과거 done 스냅샷을 캡처한 상태에서 실행한다.
    const effects = await captureEffects();
    expect(analysis.getAnalysisState().status).toBe('done');
    const cleanup = setup(effects);
    expect(analysis.getAnalysisState()).toEqual({ status: 'idle' });
    expect(session.getItem(RESULT_KEY)).toBeNull();
    expect(request.fetchFn).not.toHaveBeenCalled();
    expect(screen.navigate).not.toHaveBeenCalled();

    cleanup();
    setup(effects);
    expect(screen.navigate).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(0);
    expect(request.fetchFn).toHaveBeenCalledTimes(1);
    expect(analysis.getAnalysisState().status).toBe('running');
    expect(session.getItem(RESULT_KEY)).toBeNull();
    expect(screen.navigate).not.toHaveBeenCalled();
  });

  it.each<{ name: string; consent: boolean; patch: Partial<ConditionAnswers>; destination: string }>([
    { name: 'without consent', consent: false, patch: {}, destination: '/' },
    { name: 'without a required answer', consent: true, patch: { school: null }, destination: '/conditions/13' },
    { name: 'without the required rent band', consent: true, patch: { rentBand: null }, destination: '/conditions/13' },
    { name: 'without military information from age 35', consent: true, patch: { age: 35 }, destination: '/conditions/13' },
  ])('redirects $name without a POST', async ({ consent, patch, destination }) => {
    await prepareFlow(patch, consent);
    const request = pendingFetch();
    const effects = await captureEffects();
    setup(effects)();
    setup(effects);
    await vi.advanceTimersByTimeAsync(0);

    expect(screen.navigate).toHaveBeenCalledWith(destination, { replace: true });
    expect(screen.navigate).not.toHaveBeenCalledWith('/result', expect.anything());
    expect(request.fetchFn).not.toHaveBeenCalled();
    expect(analysis?.getAnalysisState()).toEqual({ status: 'idle' });
  });
});
