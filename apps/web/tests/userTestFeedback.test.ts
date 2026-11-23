import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import simulationText from '../../../contracts/fixtures/simulation-response.json?raw';
import { applyLink } from '../src/features/benefits/applyLink.ts';
import { toSimulationState } from '../src/features/whatIf/useSimulation.ts';
import { SimulationResult } from '../src/features/whatIf/WhatIfScreen.tsx';
import { isUnchanged, SIMULATION_ERROR_COPY } from '../src/features/whatIf/whatIfModel.ts';
import { isSimulationResult, postSimulation } from '../src/shared/api/simulations.ts';
import type { ChangeSimulationRequest, ChangeSimulationResult } from '../src/shared/api/simulations.ts';

// 2026-11 사용자 테스트(53건)에서 3명 이상이 막힌 화면의 개선 확인

const NOW = new Date('2026-11-23T12:00:00+09:00');
const SOURCE = { url: 'https://source.example.test/p' };

describe('신청 바로가기 (S3·S6)', () => {
  it('모집 중이고 신청 주소가 있을 때만 [신청하러 가기], 그 밖에는 출처 [공식 페이지 보기]', () => {
    const open = { status: 'open', starts_on: '2026-11-01', ends_on: '2026-11-30', url: 'https://apply.example.test/p' } as const;
    expect(applyLink({ application: open, source: SOURCE }, false, NOW))
      .toEqual({ url: open.url, label: '신청하러 가기 ↗', isApplication: true });
    expect(applyLink({ application: { ...open, url: null }, source: SOURCE }, false, NOW))
      .toEqual({ url: SOURCE.url, label: '공식 페이지 보기 ↗', isApplication: false });
    expect(applyLink({ application: { ...open, status: 'closed' }, source: SOURCE }, false, NOW)?.label)
      .toBe('공식 페이지 보기 ↗');
  });

  it('가상 정책이거나 정보가 없으면 링크를 만들지 않는다', () => {
    const open = { status: 'open', starts_on: null, ends_on: null, url: 'https://apply.example.test/p' } as const;
    expect(applyLink(undefined, false, NOW)).toBeNull();
    expect(applyLink({ application: open, source: SOURCE }, true, NOW)).toBeNull();
    expect(applyLink({ application: open, source: SOURCE, virtual_data: true }, false, NOW)).toBeNull();
  });
});

const request: ChangeSimulationRequest = {
  domain: 'youth',
  profile: { age: 27, region_code: null, employment_status: 'employed', income_band: 'between_15m_30m', household_type: null },
  scenario: 'income_up',
  options: { period: '1y', present_value: false },
};

function respond(status: number, body: unknown): typeof fetch {
  return () => Promise.resolve(new Response(JSON.stringify(body), { status }));
}

describe('상황 변화(S4) 오류와 변화 없음 구분', () => {
  it('서버 오류 코드에 따라 실패 이유를 나눈다', async () => {
    const outcome = async (status: number, code?: string) =>
      postSimulation(request, respond(status, { request_id: 'r', code: code ?? 'internal_error', message: 'x' }));
    expect(await outcome(503, 'dependency_unavailable')).toEqual({ status: 'error', reason: 'busy' });
    expect(await outcome(503, 'calculation_limit_exceeded')).toEqual({ status: 'error', reason: 'slow' });
    expect(await outcome(422, 'invalid_request')).toEqual({ status: 'error', reason: 'input' });
    expect(await outcome(500)).toEqual({ status: 'error', reason: 'server' });
    const offline: typeof fetch = () => Promise.reject(new TypeError('Failed to fetch'));
    expect(await postSimulation(request, offline)).toEqual({ status: 'error', reason: 'network' });
    expect((await postSimulation(request, respond(429, {}))).status).toBe('limit');
  });

  it('화면 상태에 이유를 넘기고, 이유를 모르면 일반 오류로 둔다', () => {
    expect(toSimulationState('employed', { status: 'error', reason: 'busy' })).toEqual({ status: 'error', reason: 'busy' });
    expect(toSimulationState('employed', { status: 'error' })).toEqual({ status: 'error', reason: 'server' });
  });

  it('모든 오류 안내가 제목과 설명을 가진다', () => {
    for (const copy of Object.values(SIMULATION_ERROR_COPY)) {
      expect(copy.title.length).toBeGreaterThan(5);
      expect(copy.description.length).toBeGreaterThan(10);
    }
  });

  it('계산이 끝났고 바뀌는 것이 없으면 맨 위에 "바뀌는 지원금이 없어요"를 보인다', () => {
    const fixture: unknown = JSON.parse(simulationText);
    if (!isSimulationResult(fixture)) throw new Error('invalid fixture');
    const same: ChangeSimulationResult = {
      ...fixture, lost: [], gained: [], after_total_won: fixture.before_total_won,
      annual: { before_won: fixture.annual.before_won, after_won: fixture.annual.before_won },
    };
    expect(isUnchanged(same)).toBe(true);
    expect(isUnchanged(fixture)).toBe(false);
    const html = renderToStaticMarkup(createElement(SimulationResult, { result: same, isSample: false, presentValue: false }));
    expect(html).toContain('이 상황에서는 바뀌는 지원금이 없어요');
    expect(html).toContain('계산 완료');
    expect(html.indexOf('바뀌는 지원금이 없어요')).toBeLessThan(html.indexOf('현재 추천 조합'));
    const changed = renderToStaticMarkup(createElement(SimulationResult, { result: fixture, isSample: false, presentValue: false }));
    expect(changed).not.toContain('바뀌는 지원금이 없어요');
  });
});
