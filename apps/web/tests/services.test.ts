import { describe, expect, it } from 'vitest';
import resultText from '../../../contracts/fixtures/service-recommendation-response.json?raw';
import detailText from '../../../contracts/fixtures/youth-service-benefit.json?raw';
import legacyText from '../../../contracts/fixtures/empty-response.json?raw';
import { isNormalizedBenefit } from '../src/shared/api/benefits.ts';
import { isRecommendationResponse } from '../src/shared/api/recommendations.ts';
import { isService } from '../src/shared/api/serviceValidation.ts';
import { findServiceForDetail, valueBasisText } from '../src/features/services/serviceModel.ts';

const parsedResult: unknown = JSON.parse(resultText);
const parsedDetail: unknown = JSON.parse(detailText);
if (!isRecommendationResponse(parsedResult) || !isNormalizedBenefit(parsedDetail)) {
  throw new Error('invalid synthetic F14 contract fixtures');
}
const result = parsedResult;
const detail = parsedDetail;
const priced = result.services?.[0];
const unknown = result.services?.[1];
if (!priced || !unknown) throw new Error('expected two service fixtures');

describe('F14 server response validation', () => {
  it('accepts legacy responses, explicit empty results, and unpriced services without confusing them', () => {
    expect(isRecommendationResponse(JSON.parse(legacyText))).toBe(true);
    expect(isRecommendationResponse({ ...result, services: [], services_total_won: null })).toBe(true);
    expect(isRecommendationResponse({ ...result, services: [unknown], services_total_won: null })).toBe(true);
    expect(isRecommendationResponse({ ...result, services: [unknown], services_total_won: 0 })).toBe(false);
    expect(isRecommendationResponse({ ...result, services: undefined })).toBe(false);
    expect(isRecommendationResponse({ ...result, services_total_won: undefined })).toBe(false);
  });

  it('requires the priced row and the declared sum to agree with complete evidence', () => {
    for (const override of [
      { value_source_url: null }, { value_source_url: 'data:,sample' }, { value_source_url: 'https:/test.example' },
      { value_checked_at: '2026-02-30' }, { value_basis: ' ' }, { estimated_value_won: 40_000 },
      { unit_value_won: Number.MAX_SAFE_INTEGER, quantity: 2 },
    ]) {
      expect(isService({ ...priced.service, ...override })).toBe(false);
    }
    expect(isService({ ...unknown.service, unit_value_won: 40_000 })).toBe(false);
    expect(isRecommendationResponse({ ...result, services_total_won: 100_000 })).toBe(false);
    expect(isRecommendationResponse({ ...result, services: [priced, priced] })).toBe(false);
    expect(isRecommendationResponse({
      ...result, services: [{ ...priced, application: { ...priced.application, status: 'closed' } }],
    })).toBe(false);
    expect(isRecommendationResponse({
      ...result, services: [{ ...priced, eligibility: [{ ...priced.eligibility[0], evidence: [] }] }],
    })).toBe(false);
    expect(isNormalizedBenefit({ ...detail, benefit_amount: { type: 'monthly' } })).toBe(false);
  });

  it('does not turn an out-of-range sum into a rounded server total', () => {
    const service = { ...priced.service, quantity: 1, unit_value_won: Number.MAX_SAFE_INTEGER,
      estimated_value_won: Number.MAX_SAFE_INTEGER };
    const services = [{ ...priced, service }, { ...priced, policy_id: 'youth:example:big', service }];
    expect(isRecommendationResponse({ ...result, services, services_total_won: null })).toBe(true);
    expect(isRecommendationResponse({ ...result, services, services_total_won: Number.MAX_SAFE_INTEGER })).toBe(false);
  });

  it('uses the detail public service while only linking a diagnosis from the same policy version', () => {
    expect(detail.service?.estimated_value_won).toBe(120_000);
    expect(findServiceForDetail(result, detail, false)).toBe(priced);
    expect(findServiceForDetail(null, detail, false)).toBeNull();
    expect(findServiceForDetail(result, { ...detail, id: 'other' }, false)).toBeNull();
    expect(findServiceForDetail(result, { ...detail, virtual_data: false }, false)).toBeNull();
    expect(findServiceForDetail(result, { ...detail, source: { ...detail.source, document_version: 'new' } }, false)).toBeNull();
    expect(valueBasisText(priced.service)).toBe(
      '시중 가격 1회 약 40,000원 × 3회 · 기준 [가상] 검증용 단가 기준표 · 11/01 확인',
    );
    expect(valueBasisText(unknown.service)).toBe('');
  });
});
