import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FLOW_SESSION_KEY } from '../src/features/profile/flowSession.ts';
import type { RecommendationRequest } from '../src/shared/api/recommendations.ts';
import fixtureText from '../../../contracts/fixtures/recommendation-response.json?raw';

const RESULT_KEY = 'ypm.lastResult';
const FIXTURE: unknown = JSON.parse(fixtureText);
const REQUEST: RecommendationRequest = { domain: 'youth', profile: { age: 25 } };

function memoryStorage(): Pick<Storage, 'getItem' | 'setItem' | 'removeItem'> & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return {
    data,
    getItem: (key) => data.get(key) ?? null,
    setItem: (key, value) => { data.set(key, value); },
    removeItem: (key) => { data.delete(key); },
  };
}

let session: ReturnType<typeof memoryStorage>;
let local: ReturnType<typeof memoryStorage>;
let analysis: typeof import('../src/features/benefits/analysisStore.ts') | undefined;

beforeEach(() => {
  vi.resetModules();
  vi.useFakeTimers();
  session = memoryStorage();
  local = memoryStorage();
  analysis = undefined;
  vi.stubGlobal('window', { sessionStorage: session, localStorage: local, setTimeout, clearTimeout });
});

afterEach(() => {
  analysis?.clearAnalysis();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

async function analyzedFlow(): Promise<typeof import('../src/features/profile/flowStore.ts')> {
  const flow = await import('../src/features/profile/flowStore.ts');
  flow.updateFlowState({ consent: true, remember: false });
  flow.updateAnswers({ age: 25, employmentStatus: 'job_seeker' });
  analysis = await import('../src/features/benefits/analysisStore.ts');
  vi.stubGlobal('fetch', vi.fn(async () => new Response(fixtureText, {
    headers: { 'Content-Type': 'application/json' },
  })));
  analysis.startAnalysis(REQUEST);
  await vi.waitFor(() => { expect(analysis?.getAnalysisState().status).toBe('done'); });
  return flow;
}

describe('tab-scoped conditions and analysis', () => {
  it('restores an unfinished form, consent and unknown choices without enabling local storage', async () => {
    const flow = await import('../src/features/profile/flowStore.ts');
    flow.updateFlowState({ consent: true, remember: false });
    flow.updateAnswers({
      age: 25,
      regionCode: '41110',
      regionLabel: '[가상] 경기 수원시',
      special: ['disabled'],
      parentIncome: 'unknown',
      householdSize: 'unknown',
    });
    const before = flow.getFlowState();
    expect(local.data.size).toBe(0);
    expect(session.getItem(FLOW_SESSION_KEY)).not.toBeNull();

    vi.resetModules(); // 브라우저 새로고침처럼 모듈 메모리만 다시 만든다.
    const restored = await import('../src/features/profile/flowStore.ts');
    expect(restored.getFlowState()).toEqual(before);
    expect(local.data.size).toBe(0);
  });

  it('restores the result and its conditions after refresh without a second API request', async () => {
    const flow = await analyzedFlow();
    const before = flow.getFlowState();
    vi.resetModules();
    const restored = await import('../src/features/profile/flowStore.ts');
    analysis = await import('../src/features/benefits/analysisStore.ts');
    expect(restored.getFlowState()).toEqual(before);
    expect(analysis.getAnalysisState()).toMatchObject({ status: 'done', result: FIXTURE });
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('clears both result caches when conditions change and does not resurrect them on refresh', async () => {
    const flow = await analyzedFlow();
    flow.updateAnswers({ employmentStatus: 'employed' });
    expect(analysis?.getAnalysisState()).toEqual({ status: 'idle' });
    expect(session.getItem(RESULT_KEY)).toBeNull();
    vi.resetModules();
    const restored = await import('../src/features/profile/flowStore.ts');
    analysis = await import('../src/features/benefits/analysisStore.ts');
    expect(restored.getFlowState().answers.employmentStatus).toBe('employed');
    expect(analysis.getAnalysisState()).toEqual({ status: 'idle' });
  });

  it('keeps a result when the same choice is selected again', async () => {
    const flow = await analyzedFlow();
    flow.updateAnswers({ age: 25 });
    expect(analysis?.getAnalysisState().status).toBe('done');
    expect(session.getItem(RESULT_KEY)).not.toBeNull();
  });

  it('aborts an in-flight request when conditions change and ignores a late result', async () => {
    const flow = await import('../src/features/profile/flowStore.ts');
    flow.updateFlowState({ consent: true });
    flow.updateAnswers({ age: 25 });
    analysis = await import('../src/features/benefits/analysisStore.ts');
    let finish: ((response: Response) => void) | undefined;
    let signal: AbortSignal | null | undefined;
    const fetchFn: typeof fetch = (_input, init) => {
      signal = init?.signal;
      return new Promise<Response>((resolve) => { finish = resolve; });
    };
    vi.stubGlobal('fetch', fetchFn);
    analysis.startAnalysis(REQUEST);
    flow.updateAnswers({ age: 26 });
    expect(signal?.aborted).toBe(true);
    if (finish === undefined) { throw new Error('request was not started'); }
    finish(new Response(fixtureText, { headers: { 'Content-Type': 'application/json' } }));
    await vi.runAllTimersAsync();
    expect(analysis.getAnalysisState()).toEqual({ status: 'idle' });
    expect(session.getItem(RESULT_KEY)).toBeNull();
  });

  it('removes the previous cached result as soon as a new analysis starts', async () => {
    await analyzedFlow();
    vi.stubGlobal('fetch', () => new Promise<Response>(() => { /* 요청 대기 */ }));
    analysis?.startAnalysis(REQUEST);
    expect(session.getItem(RESULT_KEY)).toBeNull();
    expect(analysis?.getAnalysisState().status).toBe('running');
  });

  it('clears local conditions, tab conditions, consent and result through the delete action', async () => {
    await analyzedFlow();
    local.setItem('ypm.conditions.v1', '{"saved_at":"2026-10-31","conditions":{}}');
    const { clearMyData } = await import('../src/features/profile/clearMyData.ts');
    clearMyData();
    expect(local.getItem('ypm.conditions.v1')).toBeNull();
    expect(session.getItem(FLOW_SESSION_KEY)).toBeNull();
    expect(session.getItem(RESULT_KEY)).toBeNull();
    vi.resetModules();
    const restored = await import('../src/features/profile/flowStore.ts');
    expect(restored.getFlowState()).toEqual({ consent: false, remember: false, answers: restored.EMPTY_ANSWERS });
  });

  it.each(['{bad json', JSON.stringify({ consent: true, remember: false, answers: { age: 25 } })])(
    'drops a damaged form and its orphaned result: %s', async (damaged) => {
      session.setItem(FLOW_SESSION_KEY, damaged);
      session.setItem(RESULT_KEY, JSON.stringify({ result: FIXTURE, isSample: false }));
      const flow = await import('../src/features/profile/flowStore.ts');
      analysis = await import('../src/features/benefits/analysisStore.ts');
      expect(flow.getFlowState().consent).toBe(false);
      expect(session.getItem(FLOW_SESSION_KEY)).toBeNull();
      expect(session.getItem(RESULT_KEY)).toBeNull();
      expect(analysis.getAnalysisState().status).toBe('idle');
    },
  );

  it('starts empty in a new tab', async () => {
    await analyzedFlow();
    vi.resetModules();
    vi.stubGlobal('window', { sessionStorage: memoryStorage(), localStorage: local, setTimeout, clearTimeout });
    const flow = await import('../src/features/profile/flowStore.ts');
    analysis = await import('../src/features/benefits/analysisStore.ts');
    expect(flow.getFlowState().answers).toEqual(flow.EMPTY_ANSWERS);
    expect(analysis.getAnalysisState().status).toBe('idle');
  });

  it('keeps in-memory entry usable when session storage is blocked', async () => {
    vi.stubGlobal('window', {
      get sessionStorage(): never { throw new Error('storage blocked'); },
      localStorage: local,
      setTimeout,
      clearTimeout,
    });
    const flow = await import('../src/features/profile/flowStore.ts');
    flow.updateFlowState({ consent: true });
    flow.updateAnswers({ age: 25 });
    expect(flow.getFlowState().answers.age).toBe(25);
    expect(local.data.size).toBe(0);
    expect(() => flow.clearFlowState()).not.toThrow();
  });
});
