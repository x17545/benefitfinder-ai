import { afterEach, describe, expect, it, vi } from 'vitest';
import { EMPTY_ANSWERS } from '../src/features/profile/flowStore.ts';
import type { ConditionAnswers } from '../src/features/profile/flowStore.ts';
import {
  clearCashFacts,
  getCashFactsForRegion,
  setCashFactsForRegion,
} from '../src/features/profile/cashFactsMemory.ts';
import type { CashFacts } from '../src/features/profile/cashFactsMemory.ts';
import { toRecommendationRequest } from '../src/features/profile/toRequest.ts';

const BASE: ConditionAnswers = {
  ...EMPTY_ANSWERS,
  age: 25,
  sidoCode: '41',
  regionCode: '41280',
  regionLabel: '경기도 고양시',
  employmentStatus: 'job_seeker',
  school: 'university_graduate',
  marital: 'single',
  living: 'alone',
  homeOwnership: 'homeless',
  householdHead: 'head',
  contractType: 'monthly_rent',
  depositBand: 'under_50m',
  rentBand: 'between_40_60',
  incomeBand: 'none',
  assetBand: 'under_122m',
  special: ['none'],
  interests: ['housing', 'employment'],
  received: ['none'],
  parentsSkipped: true,
};

const FACTS: CashFacts = {
  birth_date: '2001-03-15',
  exam: { exam_date: '2026-09-01', fee_won: 50000, same_exam_awards: 0, prior_awards_won: 0 },
  rent: { deposit_won: 10000000, contract_rent_won: 500000 },
  program_periods: [],
};

afterEach(() => {
  clearCashFacts();
  vi.unstubAllEnvs();
});

describe('cash_facts request building', () => {
  it('omits cash_facts when no facts are passed', () => {
    const request = toRecommendationRequest(BASE);
    expect(request.profile.domain_data ?? {}).not.toHaveProperty('cash_facts');
  });

  it('attaches the full cash_facts object including birth_date', () => {
    const request = toRecommendationRequest(BASE, FACTS);
    expect(request.profile.domain_data?.cash_facts).toEqual({
      birth_date: '2001-03-15',
      exam: { exam_date: '2026-09-01', fee_won: 50000, same_exam_awards: 0, prior_awards_won: 0 },
      rent: { deposit_won: 10000000, contract_rent_won: 500000 },
      program_periods: [],
    });
  });

  it('keeps an explicit empty program_periods array', () => {
    const request = toRecommendationRequest(BASE, { program_periods: [] });
    expect(request.profile.domain_data?.cash_facts).toEqual({ program_periods: [] });
  });
});

describe('cash facts module memory', () => {
  it('returns null without the local input flag', () => {
    setCashFactsForRegion('41280', FACTS);
    expect(getCashFactsForRegion('41280')).toBeNull();
  });

  it('scopes facts by region', () => {
    vi.stubEnv('VITE_LOCAL_CASH_POLICY_INPUT', '1');
    setCashFactsForRegion('41280', FACTS);
    expect(getCashFactsForRegion('41280')).toEqual(FACTS);
    expect(getCashFactsForRegion('41820')).toBeNull();
    expect(getCashFactsForRegion('41110')).toBeNull();
  });

  it('clears facts on demand', () => {
    vi.stubEnv('VITE_LOCAL_CASH_POLICY_INPUT', '1');
    setCashFactsForRegion('41820', FACTS);
    expect(getCashFactsForRegion('41820')).toEqual(FACTS);
    clearCashFacts();
    expect(getCashFactsForRegion('41820')).toBeNull();
  });
});
