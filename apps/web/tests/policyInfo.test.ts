import { createElement } from 'react';
import type { EffectCallback } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, expect, it, vi } from 'vitest';
import type { PolicyDetailState } from '../src/features/policy/usePolicyDetail.ts';
import { isNormalizedBenefit } from '../src/shared/api/benefits.ts';
import fixtureText from '../../../contracts/fixtures/youth-benefit.json?raw';

const detail = vi.hoisted(() => ({ seen: [] as PolicyDetailState[], effects: [] as EffectCallback[] }));
vi.mock('react', async (load) => ({
  ...await load<typeof import('react')>(),
  useCallback: (fn: unknown) => fn,
  useState: (initial: unknown) => [initial, (value: { state?: PolicyDetailState }) => {
    if (value.state !== undefined) detail.seen.push(value.state);
  }],
  useEffect: (effect: EffectCallback) => { detail.effects.push(effect); },
}));

afterEach(() => {
  vi.unstubAllGlobals();
  detail.seen = [];
  detail.effects = [];
});

it('fetches only the selected detail on open and preserves malformed, retry and withdrawal handling', async () => {
  const benefit: unknown = JSON.parse(fixtureText);
  if (!isNormalizedBenefit(benefit)) throw new Error('invalid fixture');
  const fetchFn = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ ...benefit, conflicts: {} })))
    .mockResolvedValueOnce(new Response(fixtureText))
    .mockResolvedValueOnce(new Response(null, { status: 404 }));
  vi.stubGlobal('fetch', fetchFn);
  vi.stubGlobal('window', { setTimeout, clearTimeout });
  const { usePolicyDetail } = await import('../src/features/policy/usePolicyDetail.ts');

  function DetailRequest(): null {
    usePolicyDetail(benefitId);
    return null;
  }
  const benefitId = benefit.id;

  for (const [index, expected] of ['error', 'ok', 'notFound'].entries()) {
    renderToStaticMarkup(createElement(DetailRequest));
    const cleanup = detail.effects.pop()?.();
    await vi.waitFor(() => expect(detail.seen[index]?.status).toBe(expected));
    if (typeof cleanup === 'function') cleanup();
    expect(fetchFn).toHaveBeenCalledTimes(index + 1);
    expect(fetchFn).toHaveBeenLastCalledWith(
      '/api/v1/benefits/' + encodeURIComponent(benefit.id), expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  }
});
