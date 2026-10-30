import type { EffectCallback } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import type { NormalizedBenefit } from '../src/shared/api/benefits.ts';
import { isNormalizedBenefit } from '../src/shared/api/benefits.ts';
import fixtureText from '../../../contracts/fixtures/youth-benefit.json?raw';

const hook = vi.hoisted(() => ({
  info: new Map<string, NormalizedBenefit>() as ReadonlyMap<string, NormalizedBenefit>,
  effects: [] as EffectCallback[],
  changes: 0,
}));
vi.mock('react', () => ({
  useState: () => [hook.info, (value: ReadonlyMap<string, NormalizedBenefit>) => {
    hook.info = value;
    hook.changes += 1;
  }],
  useEffect: (effect: EffectCallback) => { hook.effects.push(effect); },
}));

afterEach(() => vi.unstubAllGlobals());

it('leaves malformed public details out of the UI map and allows a later valid retry', async () => {
  vi.resetModules();
  const benefit: unknown = JSON.parse(fixtureText);
  if (!isNormalizedBenefit(benefit)) { throw new Error('invalid fixture'); }
  const fetchFn = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ ...benefit, conflicts: {} })))
    .mockResolvedValueOnce(new Response(fixtureText));
  vi.stubGlobal('fetch', fetchFn);
  const { usePolicyInfo } = await import('../src/features/benefits/policyInfo.ts');

  usePolicyInfo([benefit.id]);
  hook.effects.pop()?.();
  await vi.waitFor(() => expect(hook.changes).toBe(1));
  expect(hook.info.size).toBe(0);

  // 다음 화면 진입 때 실패한 상세는 다시 받으며, 검증된 값만 공유 캐시에 남긴다.
  usePolicyInfo([benefit.id]);
  hook.effects.pop()?.();
  await vi.waitFor(() => expect(hook.info.get(benefit.id)).toEqual(benefit));
  usePolicyInfo([benefit.id]);
  hook.effects.pop()?.();
  await vi.waitFor(() => expect(hook.changes).toBe(3));
  expect(fetchFn).toHaveBeenCalledTimes(2);
});
