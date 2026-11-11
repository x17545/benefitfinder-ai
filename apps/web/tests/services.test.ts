import { describe, expect, it, vi } from 'vitest';
import resultText from '../../../contracts/fixtures/recommendation-response.json?raw';
import detailText from '../../../contracts/fixtures/youth-benefit.json?raw';
import type { NormalizedBenefit } from '../src/shared/api/benefits.ts';
import type { RecommendationResponse } from '../src/shared/api/recommendations.ts';
import {
  findServiceForDetail,
  isCurrentService,
  isServiceEntry,
  readServices,
  serviceValueWon,
  servicesTotalWon,
  sortServices,
  valueBasisText,
} from '../src/features/services/serviceModel.ts';
import type { ServiceEntry } from '../src/features/services/serviceModel.ts';

const base = JSON.parse(resultText) as RecommendationResponse;
const benefit = JSON.parse(detailText) as NormalizedBenefit;

function entry(id: string, unitValue: number | null, quantity: number, verified = true): ServiceEntry {
  return {
    policy_id: id,
    service: {
      item: '면접 정장 한 벌 대여', unit: '회', quantity, unit_value_won: unitValue,
      value_basis: '[가상] 시중 대여점 3곳 가격',
      value_source_url: 'https://policy.example/virtual-price', value_checked_at: '2026-11-01',
      value_status: verified ? 'verified' : 'unknown',
    },
    eligibility: [],
  };
}

describe('S6 서비스 목록 (F14)', () => {
  it('환산 금액은 단가 × 횟수이고, 근거가 확인되지 않으면 금액을 만들지 않는다', () => {
    expect(serviceValueWon(entry('a', 40_000, 3).service)).toBe(120_000);
    expect(serviceValueWon(entry('b', null, 5).service)).toBeNull();
    expect(serviceValueWon(entry('c', 40_000, 3, false).service)).toBeNull();
  });

  it('verified라도 단가 출처·유효한 확인일·근거 설명이 없거나 곱셈 범위를 넘으면 미상이다', () => {
    const { service } = entry('a', 40_000, 3);
    for (const override of [
      { value_source_url: null }, { value_source_url: 'data:,sample' },
      { value_checked_at: '2026-02-30' }, { value_checked_at: null }, { value_basis: ' ' },
      { unit_value_won: Number.MAX_SAFE_INTEGER, quantity: 2 },
    ]) {
      expect(serviceValueWon({ ...service, ...override })).toBeNull();
    }
  });

  it('합계는 환산 기준이 없는 서비스를 빼고 더한다', () => {
    expect(servicesTotalWon([entry('a', 250_000, 1), entry('b', 40_000, 3), entry('c', null, 5)])).toBe(370_000);
    expect(servicesTotalWon([entry('a', null, 1)])).toBeNull();
    expect(servicesTotalWon([entry('a', Number.MAX_SAFE_INTEGER, 1), entry('b', 1, 1)])).toBeNull();
  });

  it('환산 금액 큰 순, 같으면 모집 중인 것 먼저, 금액 없는 것은 맨 뒤', () => {
    const sorted = sortServices(
      [entry('none', null, 1), entry('small', 30_000, 1), entry('closed', 120_000, 1), entry('open', 40_000, 3)],
      (id) => id === 'open',
    );
    expect(sorted.map((item) => item.policy_id)).toEqual(['open', 'closed', 'small', 'none']);
  });

  it('환산 기준 문구 S6-BASE', () => {
    expect(valueBasisText(entry('a', 40_000, 3).service)).toBe(
      '시중 가격 1회 약 40,000원 × 3회 · 기준 [가상] 시중 대여점 3곳 가격 · 11/01 확인',
    );
  });

  it('유효한 추천 행만 쓰고 중복·자격 미충족·근거 없는 확정 진단은 합계에서 제외한다', () => {
    const confirmed = { condition_id: 'age', status: 'not_met', evidence: [{ quote: '[가상] 예제', source: benefit.source }], reason: '' };
    const excluded = { ...entry('youth:example:excluded', 10_000, 1), eligibility: [confirmed] };
    const ungrounded = { ...entry('youth:example:invalid', 10_000, 1), eligibility: [{ ...confirmed, status: 'met', evidence: [] }] };
    const item = entry('youth:example:S9', 10_000, 1);
    const result = { ...base, services: [item, item, excluded, ungrounded, { policy_id: 'broken' }] };
    const list = readServices(result);
    expect(list.isSample).toBe(false);
    expect(list.entries.map((item) => item.policy_id)).toEqual(['youth:example:S9']);
    expect(isServiceEntry({ policy_id: 'x' })).toBe(false);
    expect(isCurrentService({ ...benefit, application: { ...benefit.application, status: 'closed' } })).toBe(false);
    expect(isCurrentService({ ...benefit, application: { ...benefit.application, status: 'upcoming' } })).toBe(false);
  });

  it('상세의 자료 종류·정책 ID·원문 버전이 맞을 때만 서비스 진단을 연결한다', () => {
    const service = entry(benefit.id, 10_000, 1);
    service.eligibility = [{
      condition_id: 'age', status: 'met', reason: '', evidence: [{ quote: '[가상] 연령', source: benefit.source }],
    }];
    const list = readServices({ ...base, virtual_data: false, services: [service] });
    expect(findServiceForDetail(list, benefit, false)).toBe(service);
    expect(findServiceForDetail(list, { ...benefit, id: 'other' }, false)).toBeNull();
    expect(findServiceForDetail(list, { ...benefit, virtual_data: true }, false)).toBeNull();
    expect(findServiceForDetail(list, benefit, true)).toBeNull();
    expect(findServiceForDetail(list, { ...benefit, source: { ...benefit.source, document_version: 'new' } }, false)).toBeNull();
  });

  it('서버가 services를 보내지 않으면 개발 서버에서만 가상 예시를 쓰고, 결과가 없으면 비어 있다', () => {
    expect(readServices(base).isSample).toBe(true);
    expect(readServices(null).entries).toEqual([]);
    expect(readServices({ ...base, services: [] })).toMatchObject({ available: true, entries: [], isSample: false });
    expect(readServices({ ...base, services: null })).toMatchObject({ available: false, entries: [], isSample: false });
    vi.stubEnv('DEV', false);
    try {
      expect(readServices(base)).toMatchObject({ available: false, entries: [], isSample: false });
    } finally {
      vi.unstubAllEnvs();
    }
  });
});
