import { describe, expect, it, vi } from 'vitest';
import simulationText from '../../../contracts/fixtures/simulation-response.json?raw';
import { isSimulationResult, postSimulation } from '../src/shared/api/simulations.ts';
import type { ChangeSimulationRequest } from '../src/shared/api/simulations.ts';
import { manText, netChange, rateBarWidth, rateSentence, signedMan } from '../src/features/whatIf/whatIfModel.ts';
import { toSimulationState } from '../src/features/whatIf/useSimulation.ts';
import { readResultBasis, withResultBasis } from '../src/features/benefits/resultBasis.ts';

const fixture: unknown = JSON.parse(simulationText);

const request: ChangeSimulationRequest = {
  domain: 'youth',
  profile: { age: 27, region_code: null, employment_status: 'employed', income_band: 'between_15m_30m', household_type: null },
  scenario: 'income_up',
  options: { period: '1y', present_value: false },
};

function respond(status: number, body: unknown): typeof fetch {
  return () => Promise.resolve(new Response(JSON.stringify(body), { status }));
}

describe('S4 상황 변화 결과', () => {
  it('계약 예제는 올바른 상황 변화 결과다', () => {
    expect(isSimulationResult(fixture)).toBe(true);
    expect(isSimulationResult({ scenario: 'income_up' })).toBe(false);
  });

  it('실제 순증가는 소득 증가에서 줄어든 연간 지원금을 뺀다 (기획서 정답 F)', () => {
    if (!isSimulationResult(fixture)) { throw new Error('invalid fixture'); }
    const net = netChange(fixture);
    expect(net?.incomeDiff).toBe(2_000_000);
    expect(net?.benefitLoss).toBe(900_000);
    expect(net?.net).toBe(1_100_000);
    expect(signedMan(net?.net ?? 0)).toBe('+110만');
    expect(signedMan(-900_000)).toBe('-90만');
    expect(manText(0)).toBe('0');
  });

  it('실효한계세율 막대는 100%에서 멈추고, 지원금이 줄지 않으면 그리지 않는다', () => {
    expect(rateBarWidth(45)).toBe(45);
    expect(rateBarWidth(120)).toBe(100);
    expect(rateBarWidth(0)).toBeNull();
    expect(rateBarWidth(null)).toBeNull();
    expect(rateSentence(45)).toBe('더 번 돈 100원 중 45원이 지원금 감소로 사라져요 (실효한계세율 45%)');
    expect(rateSentence(12.5)).toContain('12.5원');
  });

  it('서버 응답을 성공·하루 상한·없음·오류로 나눈다', async () => {
    expect((await postSimulation(request, respond(200, fixture))).status).toBe('ok');
    expect((await postSimulation(request, respond(429, {}))).status).toBe('limit');
    expect((await postSimulation(request, respond(404, {}))).status).toBe('notAvailable');
    expect((await postSimulation(request, respond(200, { scenario: 'x' }))).status).toBe('error');
  });

  it('선택한 기간과 현재가치를 전달하고 다른 시나리오·기간의 응답은 거절한다', async () => {
    if (!isSimulationResult(fixture)) throw new Error('invalid fixture');
    const url = withResultBasis('/what-if', { period: '2y', present_value: true });
    const options = readResultBasis(new URL(url, 'http://localhost').search);
    const selected = { ...request, options };
    const fetchFn = vi.fn<typeof fetch>().mockImplementation(respond(200, { ...fixture, basis_period: '2y' }));
    expect((await postSimulation(selected, fetchFn)).status).toBe('ok');
    expect(JSON.parse(String(fetchFn.mock.calls[0]?.[1]?.body))).toMatchObject({ options: { period: '2y', present_value: true } });
    expect((await postSimulation(selected, respond(200, fixture))).status).toBe('error');
    expect((await postSimulation({ ...request, scenario: 'independent' }, respond(200, fixture))).status).toBe('error');
  });

  it('비소득 시나리오나 소득이 늘지 않는 응답에는 순증가를 표시하지 않는다', () => {
    if (!isSimulationResult(fixture) || !fixture.income_change) throw new Error('invalid fixture');
    expect(netChange({ ...fixture, scenario: 'age_plus_one' })).toBeNull();
    expect(netChange({ ...fixture, income_change: { ...fixture.income_change, to_won: fixture.income_change.from_won } })).toBeNull();
  });

  it('개발 서버에서 계산 기능이 없으면 가상 결과를 쓰고, 하루 상한은 그대로 알린다', () => {
    const sample = toSimulationState('employed', { status: 'notAvailable' });
    expect(sample.status === 'ok' && sample.isSample).toBe(true);
    expect(toSimulationState('employed', { status: 'limit' }).status).toBe('limit');
    expect(toSimulationState('employed', { status: 'error' }).status).toBe('error');
    vi.stubEnv('DEV', false);
    try {
      expect(toSimulationState('employed', { status: 'notAvailable' }).status).toBe('error');
    } finally {
      vi.unstubAllEnvs();
    }
  });
});
