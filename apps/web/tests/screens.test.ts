import { createElement } from 'react';
import type { EffectCallback } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { RecommendationResponse } from '../src/shared/api/recommendations.ts';
import { isRecommendationResponse } from '../src/shared/api/recommendations.ts';
import fixtureText from '../../../contracts/fixtures/recommendation-response.json?raw';
import emptyText from '../../../contracts/fixtures/empty-response.json?raw';
import fallbackText from '../../../contracts/fixtures/fallback-response.json?raw';
import unavailableText from '../../../contracts/fixtures/pv-unknown-response.json?raw';
import servicesText from '../../../contracts/fixtures/service-recommendation-response.json?raw';

const screen = vi.hoisted(() => ({
  result: null as RecommendationResponse | null,
  location: { search: '' },
  effects: [] as EffectCallback[],
}));

// DOM 의존성 없이 실제 화면을 렌더한다. 기간/PV는 주소에서 읽고 effect 실행 시점만 지정한다.
vi.mock('react', async (load) => {
  const actual = await load<typeof import('react')>();
  return {
    ...actual,
    useSyncExternalStore: (_subscribe: unknown, getSnapshot: () => unknown) => getSnapshot(),
    useEffect: (effect: EffectCallback) => { screen.effects.push(effect); },
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
  vi.stubEnv('DEV', false);
  screen.location.search = '';
  vi.stubGlobal('window', { location: screen.location, sessionStorage: storage(), localStorage: storage() });
  screen.effects = [];
  screen.result = null;
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

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
  it('keeps cash totals but asks for a new analysis when a service snapshot has expired', async () => {
    const snapshot = fixture(servicesText);
    const services = (snapshot.services ?? []).map((entry) => ({
      ...entry, application: { ...entry.application, ends_on: '2000-01-01' },
    }));
    const flow = await import('../src/features/profile/flowStore.ts');
    flow.updateFlowState({ consent: true });
    screen.result = { ...fixture(fixtureText), services, services_total_won: snapshot.services_total_won ?? null };
    const cash = await renderResult();
    expect(cash).toContain('540만 원');
    expect(cash).toContain('조건 확인하고 다시 분석하기');
    expect(cash).not.toContain('돈 말고 받을 수 있는 도움 2개');

    const { ServicesScreen } = await import('../src/features/services/ServicesScreen.tsx');
    const list = renderToStaticMarkup(createElement(ServicesScreen));
    expect(list).toContain('서비스 모집 일정을 다시 확인해 주세요');
    expect(list).not.toContain('약 12만 원');
    expect(list).not.toContain('청년 면접 정장 대여');

    screen.result = { ...snapshot, services };
    const serviceOnly = await renderResult();
    expect(serviceOnly).toContain('조건 확인하고 다시 분석하기');
    expect(serviceOnly).not.toContain('지금 다루는 정책 중엔 맞는 게 없어요');
  });

  it('keeps the service entry with no cash, and leaves cash amounts unchanged when both exist', async () => {
    const serviceResult = fixture(servicesText);
    const { services, services_total_won } = serviceResult;
    screen.location.search = '?period=2y&pv=1';
    screen.result = serviceResult;
    const onlyServices = await renderResult();
    expect(onlyServices).toContain('확인할 수 있는 서비스가 있어요');
    expect(onlyServices).toContain('돈 말고 받을 수 있는 도움 2개');
    expect(onlyServices).toContain('/services?period=2y&amp;pv=1');
    expect(onlyServices).not.toContain('지금 다루는 정책 중엔 맞는 게 없어요');
    expect(onlyServices).not.toContain('추천 조합 요약');

    screen.location.search = '';
    screen.result = fixture(fixtureText);
    const cash = await renderResult();
    const mainSummary = (html: string) => html.match(/aria-label="추천 조합 요약"[\s\S]*?<\/section>/)?.[0];
    expect(mainSummary(cash)).toContain('540만 원');
    screen.result = Object.assign(screen.result, { services, services_total_won });
    const both = await renderResult();
    expect(mainSummary(both)).toBe(mainSummary(cash));
    expect(both).toContain('돈 말고 받을 수 있는 도움 2개');
  });

  it('labels virtual policy calculations returned by the API without the sample button', async () => {
    screen.result = { ...fixture(fixtureText), virtual_data: true };
    const html = await renderResult();
    expect(html).toContain('가상 정책으로 계산한 예제예요');
    expect(html).toContain('실제 받을 수 있는 지원금이 아니에요');
    expect(html).not.toContain('신청하러 가기');
  });

  it.each(['met', 'not_met', 'needs_review'] as const)('keeps unreviewed policies and their %s summary when no amounts can be calculated', async (status) => {
    const result = fixture(emptyText);
    result.results['1y'].pv_off.not_yet_reviewed = [{ policy_id: 'youth:example:P120', eligibility_summary: status }];
    screen.result = result;
    const html = await renderResult();
    expect(html).toContain('아직 조합 계산 전인 정책 1개');
    expect(html).toContain('정책 P120');
    expect(html).toContain(`ui-status--${status}`);
    expect(html).toContain('role="tablist"');
    expect(html).not.toContain('지금 다루는 정책 중엔 맞는 게 없어요');
    expect(html).not.toContain('aria-label="추천 조합 요약"');
    expect(html).not.toContain('0만 원');
  });

  it('keeps closed and ineligible policy lists without a calculated portfolio', async () => {
    const result = fixture(emptyText);
    result.results['1y'].pv_off.closed_now = [{ policy_id: 'youth:example:P9', last_period: '가상 지난 모집', next_expected: null }];
    result.results['1y'].pv_off.not_eligible = [{ policy_id: 'youth:example:P5', failed: ['age'] }];
    screen.result = result;
    const html = await renderResult();
    expect(html).toContain('지금은 모집이 끝난 정책 1개');
    expect(html).toContain('가상 지난 모집');
    expect(html).toContain('조건이 맞지 않는 정책 1개');
    expect(html).toContain('ui-status--not_met');
  });

  it('keeps tabs when only a later period has supplementary policies', async () => {
    const result = fixture(emptyText);
    result.results['2y'].pv_off.not_yet_reviewed = [{ policy_id: 'youth:example:P120', eligibility_summary: 'needs_review' }];
    screen.result = result;
    const initial = await renderResult();
    expect(initial).toContain('role="tablist"');
    expect(initial).not.toContain('정책 P120');
    screen.location.search = '?period=2y';
    expect(await renderResult()).toContain('정책 P120');
  });

  it('does not turn unavailable calculations into a no-eligible-policy claim', async () => {
    const result = fixture(emptyText);
    for (const period of Object.values(result.results)) {
      for (const view of [period.pv_off, period.pv_on]) {
        view.calculation_status = 'unavailable';
        view.unavailable_reason = 'unknown_payment_timing';
      }
    }
    screen.result = result;
    const html = await renderResult();
    expect(html).toContain('이 기준으로는 계산할 수 없어요');
    expect(html).toContain('role="tablist"');
    expect(html).not.toContain('지금 다루는 정책 중엔 맞는 게 없어요');
  });

  it('keeps tabs when 1y is empty and shows the 2y portfolio when selected', async () => {
    const result = fixture(fixtureText);
    result.view = 'empty';
    result.results['1y'] = fixture(emptyText).results['1y'];
    screen.result = result;
    const initial = await renderResult();
    expect(initial).toContain('role="tablist"');
    expect(initial).toContain('지금 다루는 정책 중엔 맞는 게 없어요');
    expect(initial).not.toContain('지원금 0개를 찾았어요');

    screen.location.search = '?period=2y';
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
    screen.location.search = '?period=2y';
    const html = await renderResult();
    expect(html).toContain('300만 원');
    expect(html).not.toContain('150만 원');
    expect(html).not.toContain('조합 비교');
  });

  it('shows a selected empty period without hiding the other tabs', async () => {
    const result = fixture(fixtureText);
    result.results['2y'] = fixture(emptyText).results['2y'];
    screen.result = result;
    screen.location.search = '?period=2y';
    const html = await renderResult();
    expect(html).toContain('role="tablist"');
    expect(html).toContain('지금 다루는 정책 중엔 맞는 게 없어요');
    expect(html).not.toContain('aria-label="추천 조합 요약"');
  });

  it('distinguishes an unavailable PV calculation from an empty result', async () => {
    screen.result = fixture(unavailableText);
    screen.location.search = '?pv=1';
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
