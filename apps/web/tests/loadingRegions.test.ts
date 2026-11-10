import { createElement } from 'react';
import type { EffectCallback } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { RegionOptionsState } from '../src/features/profile/regionOptions.ts';
import fixtureText from '../../../contracts/fixtures/recommendation-response.json?raw';

const screen = vi.hoisted(() => ({
  state: { status: 'loading' } as RegionOptionsState,
  effects: [] as EffectCallback[], navigate: vi.fn(), retry: vi.fn(),
}));
vi.mock('react', async (load) => ({
  ...await load<typeof import('react')>(),
  useSyncExternalStore: (_subscribe: unknown, getSnapshot: () => unknown) => getSnapshot(),
  useEffect: (effect: EffectCallback) => { screen.effects.push(effect); },
}));
vi.mock('../src/app/router.ts', async (load) => ({
  ...await load<typeof import('../src/app/router.ts')>(), navigate: screen.navigate,
}));
vi.mock('../src/features/profile/regionOptions.ts', () => ({
  useRegionOptions: () => ({ state: screen.state, retry: screen.retry }),
}));
const regions = [{ sido_code: '41', sido_name: '가상도', sigungu: [{ code: '41110', name: '가상시' }] }];
const cleanups: (() => void)[] = [];

beforeEach(async () => {
  vi.resetModules(); vi.useFakeTimers();
  vi.stubGlobal('window', { setTimeout, clearTimeout });
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async () => new Response(fixtureText, {
    headers: { 'Content-Type': 'application/json' },
  })));
  screen.effects = []; screen.navigate.mockClear(); screen.retry.mockClear();
  screen.state = { status: 'ready', regions };
  const flow = await import('../src/features/profile/flowStore.ts');
  flow.updateFlowState({ consent: true });
  flow.updateAnswers({
    age: 25, sidoCode: '41', regionCode: '41110', regionLabel: '가상도 가상시',
    employmentStatus: 'job_seeker', school: 'university_graduate', marital: 'single',
    living: 'alone', homeOwnership: 'homeless', householdHead: 'head',
    contractType: 'monthly_rent', depositBand: 'under_50m', rentBand: 'between_40_60',
    incomeBand: 'none', assetBand: 'under_122m', special: ['none'],
    interests: ['employment', 'housing'], received: ['none'], parentsSkipped: true,
  });
});
afterEach(async () => {
  cleanups.splice(0).forEach((cleanup) => { cleanup(); });
  (await import('../src/features/benefits/analysisStore.ts')).clearAnalysis();
  vi.clearAllTimers(); vi.useRealTimers(); vi.unstubAllGlobals();
});

async function mount(): Promise<string> {
  screen.effects = [];
  const { LoadingScreen } = await import('../src/features/benefits/LoadingScreen.tsx');
  const html = renderToStaticMarkup(createElement(LoadingScreen));
  for (const effect of screen.effects) {
    const cleanup = effect();
    if (typeof cleanup === 'function') { cleanups.push(cleanup); }
  }
  await vi.advanceTimersByTimeAsync(0);
  return html;
}

describe('current regions at the analysis entry point', () => {
  it.each(['loading', 'error', 'empty'] as const)('never posts while catalog is %s', async (status) => {
    screen.state = { status };
    const html = await mount();
    expect(fetch).not.toHaveBeenCalled();
    if (status !== 'loading') { expect(html).toContain('다시 시도'); }
  });

  it.each([['99', '99999'], ['11', '41110'], ['41', '41999']])('blocks restored %s/%s', async (sidoCode, regionCode) => {
    const flow = await import('../src/features/profile/flowStore.ts');
    flow.updateAnswers({ sidoCode, regionCode });
    await mount();
    expect(fetch).not.toHaveBeenCalled();
    expect(screen.navigate).toHaveBeenCalledWith('/conditions/2', { replace: true });
    expect(flow.getFlowState().answers.regionCode).toBeNull();
  });

  it('posts the verified parent/child codes once', async () => {
    await mount();
    expect(fetch).toHaveBeenCalledTimes(1);
    const call = vi.mocked(fetch).mock.calls[0];
    const payload = JSON.parse(String(call?.[1]?.body)) as { profile: { region_code: string; domain_data: { region: { sido_code: string } } } };
    expect(payload.profile.region_code).toBe('41110');
    expect(payload.profile.domain_data.region.sido_code).toBe('41');
  });
});
