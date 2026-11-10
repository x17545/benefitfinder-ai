import { createElement } from 'react';
import type { EffectCallback } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { RegionOptionsState } from '../src/features/profile/regionOptions.ts';

const controls = vi.hoisted(() => ({
  state: { status: 'loading' } as RegionOptionsState,
  effects: [] as EffectCallback[],
}));
vi.mock('react', async (load) => ({
  ...await load<typeof import('react')>(),
  useSyncExternalStore: (_subscribe: unknown, getSnapshot: () => unknown) => getSnapshot(),
  useEffect: (effect: EffectCallback) => { controls.effects.push(effect); },
}));
vi.mock('../src/features/profile/regionOptions.ts', () => ({
  useRegionOptions: () => ({ state: controls.state, retry: vi.fn() }),
}));

const regions = [{ sido_code: '41', sido_name: '가상도', sigungu: [
  { code: '41110', name: '가상시' }, { code: '41130', name: '다른시' },
] }];

beforeEach(async () => {
  vi.resetModules();
  controls.effects = [];
  controls.state = { status: 'ready', regions };
  const flow = await import('../src/features/profile/flowStore.ts');
  flow.updateAnswers({
    age: 25, sidoCode: '41', regionCode: '41110', regionLabel: 'old label',
    employmentStatus: 'job_seeker', school: 'university_graduate', marital: 'single',
    living: 'alone', homeOwnership: 'homeless', householdHead: 'head',
    contractType: 'monthly_rent', depositBand: 'under_50m', rentBand: 'between_40_60',
    incomeBand: 'none', assetBand: 'under_122m', special: ['none'],
    interests: ['employment', 'housing'], received: ['none'], parentsSkipped: true,
  });
});

async function confirmation(): Promise<string> {
  const { ConfirmStep } = await import('../src/features/profile/steps/ConfirmStep.tsx');
  return renderToStaticMarkup(createElement(ConfirmStep));
}

describe('region validation at screen boundaries', () => {
  it.each(['loading', 'empty', 'error'] as const)('blocks confirmation while catalog is %s', async (status) => {
    controls.state = { status };
    const html = await confirmation();
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>내 조합 찾기<\/button>/);
    if (status !== 'loading') { expect(html).toContain('다시 시도'); }
  });

  it('blocks stale restored codes even when every other step is complete', async () => {
    const flow = await import('../src/features/profile/flowStore.ts');
    flow.updateAnswers({ sidoCode: '99', regionCode: '99999' });
    expect(await confirmation()).toContain('지역이 변경됐어요');
    controls.effects.forEach((effect) => effect());
    expect(flow.getFlowState().answers.regionCode).toBeNull();
    expect(flow.getFlowState().answers.sidoCode).toBeNull();
  });

  it('preserves valid codes and refreshes the restored display name', async () => {
    const html = await confirmation();
    expect(html).toMatch(/<button(?![^>]*disabled)[^>]*>내 조합 찾기<\/button>/);
    controls.effects.forEach((effect) => effect());
    const flow = await import('../src/features/profile/flowStore.ts');
    expect(flow.getFlowState().answers.regionLabel).toBe('가상도 가상시');
  });

  it('shows distinct failure and empty messages and prevents step-2 continuation', async () => {
    const { RegionStep } = await import('../src/features/profile/steps/RegionStep.tsx');
    for (const status of ['error', 'empty'] as const) {
      controls.state = { status };
      const html = renderToStaticMarkup(createElement(RegionStep));
      expect(html).toContain(status === 'error' ? '불러오지 못했어요' : '목록이 비어 있어요');
      expect(html).toMatch(/<button[^>]*disabled=""[^>]*>다음<\/button>/);
      expect(html).not.toContain('[가상]');
    }
  });

  it('gates saved-condition reuse until verified and offers stale codes a new selection', async () => {
    const flow = await import('../src/features/profile/flowStore.ts');
    const { SavedConditionsCard } = await import('../src/features/profile/SavedConditionsCard.tsx');
    const saved = { savedAt: '2026-11-07T00:00:00Z', answers: flow.getFlowState().answers };
    controls.state = { status: 'loading' };
    let html = renderToStaticMarkup(createElement(SavedConditionsCard, { saved, onClear: vi.fn() }));
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>이 조건으로 다시 보기<\/button>/);
    controls.state = { status: 'ready', regions };
    html = renderToStaticMarkup(createElement(SavedConditionsCard, {
      saved: { ...saved, answers: { ...saved.answers, regionCode: '41999' } }, onClear: vi.fn(),
    }));
    expect(html).toContain('지역 다시 선택하기');
  });
});
