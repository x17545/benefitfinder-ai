import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ServiceMeta } from '../src/shared/api/generated/meta.ts';

const api = vi.hoisted(() => ({ read: vi.fn() }));
vi.mock('../src/shared/api/meta.ts', () => ({ fetchServiceMeta: api.read }));
vi.mock('react', async (load) => ({
  ...await load<typeof import('react')>(),
  useSyncExternalStore: (_subscribe: unknown, snapshot: () => unknown) => snapshot(),
}));

const published: ServiceMeta = {
  data_updated_at: '2026-11-12T23:30:00Z', base_rate: 0.025,
  base_rate_status: 'verified', daily_remaining: 87, virtual_data: false,
};
let stop: (() => void) | undefined;

function storage(): Pick<Storage, 'getItem' | 'setItem' | 'removeItem'> {
  const values = new Map<string, string>();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => { values.set(key, value); },
    removeItem: (key) => { values.delete(key); },
  };
}

beforeEach(() => {
  vi.resetModules();
  api.read.mockReset().mockResolvedValue(published);
  vi.stubGlobal('window', Object.assign(new EventTarget(), {
    sessionStorage: storage(), localStorage: storage(), location: { search: '' },
  }));
});
afterEach(() => {
  stop?.();
  stop = undefined;
  vi.unstubAllGlobals();
});

async function start(meta: ServiceMeta = published): Promise<void> {
  api.read.mockResolvedValue(meta);
  const store = await import('../src/shared/api/serviceMetaStore.ts');
  stop = store.startServiceMeta();
  await store.refreshServiceMeta();
}

async function render(): Promise<{ footer: string; start: string; about: string }> {
  const { SiteFooter } = await import('../src/shared/ui/SiteFooter.tsx');
  const { StartScreen } = await import('../src/features/profile/StartScreen.tsx');
  const { AboutScreen } = await import('../src/features/info/AboutScreen.tsx');
  return {
    footer: renderToStaticMarkup(createElement(SiteFooter)),
    start: renderToStaticMarkup(createElement(StartScreen)),
    about: renderToStaticMarkup(createElement(AboutScreen)),
  };
}

describe('public metadata on common screens', () => {
  it('shows published state, KST time, rate and shared remaining count from one read', async () => {
    await start();
    const html = await render();
    expect(html.footer).toContain('게시 자료 갱신');
    expect(html.footer).toContain('2026. 11. 13. 08:30');
    expect(html.footer).toMatch(/datetime="2026-11-12T23:30:00Z"/i);
    expect(html.footer).toContain('KST');
    expect(html.footer).toContain('연 2.5%');
    expect(html.footer).toContain('남은 분석 87회 · 전체 이용자 공유');
    for (const page of Object.values(html)) expect(page).not.toContain('가상 정책');
    expect(api.read).toHaveBeenCalledTimes(1);
  });

  it('marks virtual policy data separately without certifying its example rate', async () => {
    await start({ ...published, virtual_data: true, base_rate_status: 'unknown' });
    const html = await render();
    expect(html.footer).toContain('가상 자료 기준 시각');
    expect(html.footer).toContain('가상 예제 할인율 확인 필요');
    expect(html.footer).not.toContain('연 2.5%');
    expect(html.start).toContain('현재는 가상 정책');
    expect(html.about).toContain('실제 받을 수 있는 지원금이 아니에요');
    expect(html.footer).not.toContain('게시 자료 갱신');
  });

  it('keeps zero percent and zero remaining distinct from missing values', async () => {
    await start({ ...published, base_rate: 0, daily_remaining: 0 });
    const html = await render();
    expect(html.footer).toContain('연 0%');
    expect(html.footer).toContain('남은 분석 0회');
    expect(html.footer).not.toContain('할인율 확인 필요');
  });

  it('shows unknown rate without turning it into zero', async () => {
    await start({ ...published, base_rate: null, base_rate_status: 'unknown' });
    const html = await render();
    expect(html.footer).toContain('할인율 확인 필요');
    expect(html.footer).not.toContain('연 0%');
  });

  it('does not infer data mode, rate, count or refresh schedule during loading', async () => {
    const html = await render();
    expect(html.footer).toContain('자료 상태를 확인하고 있어요');
    expect(html.footer).not.toContain('가상 정책');
    expect(html.footer).not.toContain('남은 분석');
    expect(html.footer).not.toContain('매일 새벽 3시');
  });

  it('clears previous success on failure and permits retry without fictional fallback', async () => {
    await start();
    api.read.mockRejectedValueOnce(new Error('PRIVATE internal failure'));
    const store = await import('../src/shared/api/serviceMetaStore.ts');
    await store.refreshServiceMeta();
    const html = await render();
    expect(html.footer).toContain('자료 상태를 확인하지 못했어요');
    expect(html.footer).toContain('다시 확인');
    for (const value of ['남은 분석', '게시 자료 갱신', '가상 정책', 'PRIVATE']) {
      expect(html.footer).not.toContain(value);
    }
    await store.refreshServiceMeta();
    expect((await render()).footer).toContain('남은 분석 87회');
  });
});

describe('metadata request lifetime', () => {
  it('discards old responses when a newer request finishes first', async () => {
    const store = await import('../src/shared/api/serviceMetaStore.ts');
    stop = store.startServiceMeta();
    let resolveOld: ((meta: ServiceMeta) => void) | undefined;
    api.read.mockImplementationOnce(() => new Promise<ServiceMeta>((resolve) => { resolveOld = resolve; }));
    const old = store.refreshServiceMeta();
    const signal = api.read.mock.calls[0][0].signal as AbortSignal;
    await store.refreshServiceMeta();
    expect(signal.aborted).toBe(true);
    resolveOld?.({ ...published, virtual_data: true, daily_remaining: 1 });
    await old;
    expect(store.getServiceMetaState()).toEqual({ status: 'ok', meta: published });
  });

  it('aborts on unmount and refreshes on focus only while mounted', async () => {
    await start();
    window.dispatchEvent(new Event('focus'));
    await Promise.resolve();
    expect(api.read).toHaveBeenCalledTimes(2);
    let resolveLate: ((meta: ServiceMeta) => void) | undefined;
    api.read.mockImplementationOnce(() => new Promise<ServiceMeta>((resolve) => { resolveLate = resolve; }));
    const store = await import('../src/shared/api/serviceMetaStore.ts');
    const pending = store.refreshServiceMeta();
    const signal = api.read.mock.calls[2][0].signal as AbortSignal;
    stop?.();
    stop = undefined;
    expect(signal.aborted).toBe(true);
    resolveLate?.(published);
    await pending;
    window.dispatchEvent(new Event('focus'));
    await store.refreshServiceMeta();
    expect(api.read).toHaveBeenCalledTimes(3);
    expect(store.getServiceMetaState()).toEqual({ status: 'loading' });
  });
});
