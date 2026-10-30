// S2에서 고른 값 → 서버에 보내는 추천 요청 (contracts/v1 recommendation-request).
import { MILITARY_MONTHS } from '../../domains/youth/conditionOptions.ts';
import type { RecommendationRequest } from '../../shared/api/generated/recommendation-request.ts';
import type { YouthProfileExtension } from '../../shared/api/generated/youth-profile.ts';
import { needsDeposit, needsMilitary, needsRent } from './answerRules.ts';
import type { ConditionAnswers } from './flowStore.ts';

type YouthDomainData = NonNullable<YouthProfileExtension['domain_data']>;

/** '모름'은 서버에 미상(null)으로 보낸다 */
function known<T>(value: T | 'unknown' | null): T | null {
  return value === 'unknown' ? null : value;
}

export function toRecommendationRequest(answers: ConditionAnswers): RecommendationRequest {
  const married = answers.marital === 'married';

  const domainData: YouthDomainData = {
    // 35세 미만은 묻지 않으므로 미상(null)
    military_months_range:
      needsMilitary(answers.age) && answers.military !== null ? MILITARY_MONTHS[answers.military] : null,
    region: answers.sidoCode !== null ? { sido_code: answers.sidoCode } : null,
    school: answers.school,
    marital: answers.marital,
    housing: {
      living: answers.living,
      home_ownership: answers.homeOwnership,
      household_head: answers.householdHead,
    },
    contract: {
      type: answers.contractType,
      deposit_band: needsDeposit(answers.contractType) ? answers.depositBand : null,
      rent_band: needsRent(answers.contractType) ? answers.rentBand : null,
    },
    asset_band: answers.assetBand,
    special: answers.special,
    interests: answers.interests,
    received: answers.received,
    // 기혼이면 부모님 정보는 보내지 않는다
    household: married
      ? { household_size: null, parent_income_band: null, parent_asset_band: null }
      : {
          household_size: known(answers.householdSize),
          parent_income_band: known(answers.parentIncome),
          parent_asset_band: known(answers.parentAsset),
        },
  };

  return {
    domain: 'youth',
    profile: {
      age: answers.age,
      region_code: answers.regionCode,
      employment_status: answers.employmentStatus,
      income_band: answers.incomeBand,
      household_type: null,
      domain_data: domainData,
    },
    options: { period: '1y', present_value: false },
  };
}
