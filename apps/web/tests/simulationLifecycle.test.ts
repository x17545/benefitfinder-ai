import { createElement } from 'react';
import type { EffectCallback } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ChangeSimulationRequest, SimulationOutcome } from '../src/shared/api/simulations.ts';
import request from '../../../contracts/fixtures/simulation-request.json';

const screen = vi.hoisted(() => ({ effects: [] as EffectCallback[], post: vi.fn(), refresh: vi.fn() }));
vi.mock('react', async (load) => ({
  ...await load<typeof import('react')>(),
  useEffect: (effect: EffectCallback) => { screen.effects.push(effect); },
}));
vi.mock('../src/shared/api/simulations.ts', async (load) => ({
  ...await load<typeof import('../src/shared/api/simulations.ts')>(), postSimulation: screen.post,
}));
vi.mock('../src/shared/api/serviceMetaStore.ts', () => ({ refreshServiceMeta: screen.refresh }));

const cleanups: (() => void)[] = [];

beforeEach(() => {
  vi.resetModules();
  vi.useFakeTimers();
  screen.effects = [];
  screen.post.mockReset().mockImplementation(() => new Promise<SimulationOutcome>(() => {}));
  screen.refresh.mockReset();
  vi.stubGlobal('window', { setTimeout, clearTimeout });
});
afterEach(() => {
  cleanups.splice(0).forEach((cleanup) => { cleanup(); });
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

async function effect(scenario?: 'employed' | 'income_up'): Promise<EffectCallback> {
  const { useSimulation } = await import('../src/features/whatIf/useSimulation.ts');
  function Probe(): null {
    useSimulation(scenario === undefined ? null : { ...request, domain: 'youth', scenario } as ChangeSimulationRequest);
    return null;
  }
  renderToStaticMarkup(createElement(Probe));
  const captured = screen.effects.pop();
  if (captured === undefined) throw new Error('effect was not captured');
  return captured;
}

function setup(callback: EffectCallback): () => void {
  const cleanup = callback();
  if (typeof cleanup !== 'function') return () => {};
  const stop = (): void => { cleanup(); };
  cleanups.push(stop);
  return stop;
}

describe('simulation request lifetime', () => {
  it('sends once after setup-cleanup-setup instead of charging twice in StrictMode', async () => {
    const callback = await effect('employed');
    setup(callback)();
    setup(callback);
    await vi.runOnlyPendingTimersAsync();
    expect(screen.post).toHaveBeenCalledTimes(1);
    expect(screen.post.mock.calls[0][2].aborted).toBe(false);
  });

  it('sends nothing when navigation cancels before dispatch', async () => {
    setup(await effect('employed'))();
    await vi.runOnlyPendingTimersAsync();
    expect(screen.post).not.toHaveBeenCalled();
  });

  it('cancels the old scheduled scenario and sends only the latest one', async () => {
    setup(await effect('employed'))();
    setup(await effect('income_up'));
    await vi.runOnlyPendingTimersAsync();
    expect(screen.post).toHaveBeenCalledTimes(1);
    expect(screen.post.mock.calls[0][0].scenario).toBe('income_up');
  });

  it('aborts an in-flight request and ignores its late result without refreshing metadata', async () => {
    let finish: ((outcome: SimulationOutcome) => void) | undefined;
    screen.post.mockImplementationOnce(() => new Promise<SimulationOutcome>((resolve) => { finish = resolve; }));
    const stop = setup(await effect('employed'));
    await vi.runOnlyPendingTimersAsync();
    const signal = screen.post.mock.calls[0][2] as AbortSignal;
    stop();
    expect(signal.aborted).toBe(true);
    finish?.({ status: 'limit' });
    await Promise.resolve();
    expect(screen.refresh).not.toHaveBeenCalled();
  });

  it('refreshes shared metadata once after a completed attempt', async () => {
    screen.post.mockResolvedValueOnce({ status: 'limit' });
    setup(await effect('employed'));
    await vi.runOnlyPendingTimersAsync();
    expect(screen.post).toHaveBeenCalledTimes(1);
    expect(screen.refresh).toHaveBeenCalledTimes(1);
  });

  it('does not start a request without complete input', async () => {
    setup(await effect());
    await vi.runOnlyPendingTimersAsync();
    expect(screen.post).not.toHaveBeenCalled();
  });
});
