import { createElement } from 'react';
import type { EffectCallback } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ChangeSimulationRequest, SimulationOutcome } from '../src/shared/api/simulations.ts';
import request from '../../../contracts/fixtures/simulation-request.json';

const screen = vi.hoisted(() => ({ effects: [] as EffectCallback[], post: vi.fn(), refresh: vi.fn(), updates: vi.fn() }));
vi.mock('react', async (load) => {
  const actual = await load<typeof import('react')>();
  return {
    ...actual,
    useEffect: (effect: EffectCallback) => { screen.effects.push(effect); },
    useState: <T,>(initial: T | (() => T)) => {
      const [value, setValue] = actual.useState(initial);
      return [value, (next: T | ((current: T) => T)) => {
        screen.updates(next);
        setValue(next);
      }];
    },
  };
});
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
  screen.updates.mockReset();
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

  it('starts without newer AbortSignal APIs and ends a stalled request with an error', async () => {
    vi.stubGlobal('AbortSignal', {});
    let finish: ((outcome: SimulationOutcome) => void) | undefined;
    screen.post.mockImplementationOnce(() => new Promise<SimulationOutcome>((resolve) => { finish = resolve; }));
    setup(await effect('employed'));
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.post).toHaveBeenCalledTimes(1);
    const signal = screen.post.mock.calls[0][2] as AbortSignal;
    expect(signal.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(signal.aborted).toBe(true);
    expect(screen.updates).toHaveBeenCalledExactlyOnceWith(
      expect.objectContaining({ state: { status: 'error' } }),
    );
    finish?.({ status: 'limit' });
    await Promise.resolve();
    expect(screen.updates).toHaveBeenCalledTimes(1);
  });

  it('enters the retryable error state when the request rejects', async () => {
    screen.post.mockRejectedValueOnce(new Error('fixture network error'));
    setup(await effect('employed'));
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.updates).toHaveBeenCalledExactlyOnceWith(
      expect.objectContaining({ state: { status: 'error' } }),
    );
  });

  it('does not start a request without complete input', async () => {
    setup(await effect());
    await vi.runOnlyPendingTimersAsync();
    expect(screen.post).not.toHaveBeenCalled();
  });
});

describe('policy detail request lifetime', () => {
  it('starts without newer AbortSignal APIs and makes an unresponsive detail retryable', async () => {
    vi.stubGlobal('AbortSignal', {});
    let finish: ((response: Response) => void) | undefined;
    const fetchDetail = vi.fn(() => new Promise<Response>((resolve) => { finish = resolve; }));
    vi.stubGlobal('fetch', fetchDetail);
    const { usePolicyDetail } = await import('../src/features/policy/usePolicyDetail.ts');
    function Probe(): null {
      usePolicyDetail('youth:fictional:detail');
      return null;
    }
    renderToStaticMarkup(createElement(Probe));
    const captured = screen.effects.pop();
    if (captured === undefined) throw new Error('effect was not captured');
    setup(captured);
    expect(fetchDetail).toHaveBeenCalledWith(
      '/api/v1/benefits/youth%3Afictional%3Adetail', { signal: expect.any(Object) },
    );
    const signal = vi.mocked(fetch).mock.calls[0][1]?.signal;
    expect(signal?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(signal?.aborted).toBe(true);
    expect(screen.updates).toHaveBeenCalledExactlyOnceWith(
      expect.objectContaining({ state: { status: 'error' } }),
    );
    finish?.(new Response(null, { status: 404 }));
    await Promise.resolve();
    await Promise.resolve();
    expect(screen.updates).toHaveBeenCalledTimes(1);
  });
});
