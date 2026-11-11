import { describe, expect, it } from 'vitest';
import resultText from '../../../contracts/fixtures/recommendation-response.json?raw';
import type { RecommendationResponse } from '../src/shared/api/recommendations.ts';
import {
  findService,
  isServiceEntry,
  readServices,
  serviceValueWon,
  servicesTotalWon,
  sortServices,
  valueBasisText,
} from '../src/features/services/serviceModel.ts';
import type { ServiceEntry } from '../src/features/services/serviceModel.ts';

const base = JSON.parse(resultText) as RecommendationResponse;

function entry(id: string, unitValue: number | null, quantity: number, verified = true): ServiceEntry {
  return {
    policy_id: id,
    service: {
      item: '면접 정장 한 벌 대여', unit: '회', quantity, unit_value_won: unitValue,
      value_basis: '[가상] 시중 대여점 3곳 가격', value_source_url: null, value_checked_at: '2026-11-01',
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

  it('합계는 환산 기준이 없는 서비스를 빼고 더한다', () => {
    expect(servicesTotalWon([entry('a', 250_000, 1), entry('b', 40_000, 3), entry('c', null, 5)])).toBe(370_000);
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

  it('서버가 services를 보내면 모양이 맞는 항목만 쓴다', () => {
    const result = { ...base, services: [entry('youth:example:S9', 10_000, 1), { policy_id: 'broken' }] };
    const list = readServices(result as RecommendationResponse);
    expect(list.isSample).toBe(false);
    expect(list.entries.map((item) => item.policy_id)).toEqual(['youth:example:S9']);
    expect(findService(list, 'youth:example:S9')?.service.quantity).toBe(1);
    expect(isServiceEntry({ policy_id: 'x' })).toBe(false);
  });

  it('서버가 services를 보내지 않으면 개발 서버에서만 가상 예시를 쓰고, 결과가 없으면 비어 있다', () => {
    expect(readServices(base).isSample).toBe(true);
    expect(readServices(null).entries).toEqual([]);
  });
});
