import { describe, expect, it } from 'vitest';
import { EMPTY_ANSWERS } from '../src/features/profile/flowStore.ts';
import type { ConditionAnswers } from '../src/features/profile/flowStore.ts';
import { toRecommendationRequest } from '../src/features/profile/toRequest.ts';

const USER_A: ConditionAnswers = {
  ...EMPTY_ANSWERS,
  age: 25,
  sidoCode: '41',
  regionCode: '41110',
  regionLabel: '경기도 수원시',
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

describe('toRecommendationRequest', () => {
  it('builds the contract request for user A', () => {
    expect(toRecommendationRequest(USER_A)).toEqual({
      domain: 'youth',
      profile: {
        age: 25,
        region_code: '41110',
        employment_status: 'job_seeker',
        income_band: 'none',
        household_type: null,
        domain_data: {
          military_months_range: null,
          region: { sido_code: '41' },
          school: 'university_graduate',
          marital: 'single',
          housing: { living: 'alone', home_ownership: 'homeless', household_head: 'head' },
          contract: { type: 'monthly_rent', deposit_band: 'under_50m', rent_band: 'between_40_60' },
          asset_band: 'under_122m',
          special: ['none'],
          interests: ['housing', 'employment'],
          received: ['none'],
          household: { household_size: null, parent_income_band: null, parent_asset_band: null },
        },
      },
      options: { period: '1y', present_value: false },
    });
  });

  it('sends military months only from age 35', () => {
    const request = toRecommendationRequest({ ...USER_A, age: 36, military: 'from_19_to_24' });
    expect(request.profile.domain_data?.military_months_range).toEqual({ min: 19, max: 24 });
  });

  it('sends "unknown" parent answers as null and drops them when married', () => {
    const single = toRecommendationRequest({
      ...USER_A,
      parentsSkipped: false,
      householdSize: 'unknown',
      parentIncome: 'between_3m_5m',
      parentAsset: 'unknown',
    });
    expect(single.profile.domain_data?.household).toEqual({
      household_size: null,
      parent_income_band: 'between_3m_5m',
      parent_asset_band: null,
    });

    const married = toRecommendationRequest({ ...USER_A, marital: 'married', parentIncome: 'over_7m' });
    expect(married.profile.domain_data?.household).toEqual({
      household_size: null,
      parent_income_band: null,
      parent_asset_band: null,
    });
  });

  it('drops the rent band when the contract is jeonse', () => {
    const request = toRecommendationRequest({ ...USER_A, contractType: 'jeonse' });
    expect(request.profile.domain_data?.contract).toEqual({
      type: 'jeonse',
      deposit_band: 'under_50m',
      rent_band: null,
    });
  });
});
