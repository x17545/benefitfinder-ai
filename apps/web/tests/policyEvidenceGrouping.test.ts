import { describe, expect, it } from 'vitest';
import type { ConditionResult } from '../src/features/policy/policyDetailModel.ts';
import { partitionPolicyEvidence } from '../src/features/policy/policyDetailModel.ts';

function condition(
  condition_id: string,
  status: ConditionResult['status'] = 'needs_review',
): ConditionResult {
  return { condition_id, status, reason: '', evidence: [] };
}

describe('정책 상세의 자격 조건과 공식 근거 분리', () => {
  it('공식 안내 10개 구간과 금액 근거 1개를 일반 자격 조건과 분리한다', () => {
    const items = [
      ...Array.from({ length: 10 }, (_, index) =>
        condition('official_source_terms_' + String(index))),
      condition('calculated_component'),
    ];

    const grouped = partitionPolicyEvidence(items);

    expect(grouped.eligibility).toEqual([]);
    expect(grouped.officialTerms).toHaveLength(10);
    expect(grouped.calculationEvidence).toHaveLength(1);
    expect([
      ...grouped.eligibility,
      ...grouped.officialTerms,
      ...grouped.calculationEvidence,
    ]).toHaveLength(11);
  });

  it('일반 자격 조건과 판정 상태를 변경하지 않는다', () => {
    const age = condition('age', 'met');
    const income = condition('income_max', 'not_met');
    const source = condition('official_source_terms_0');
    const amount = condition('calculated_component');

    const grouped = partitionPolicyEvidence([age, source, income, amount]);

    expect(grouped.eligibility).toEqual([age, income]);
    expect(grouped.officialTerms).toEqual([source]);
    expect(grouped.calculationEvidence).toEqual([amount]);
  });

  it('비슷한 이름의 다른 조건을 잘못 분리하지 않는다', () => {
    const custom = condition('official_source_terms_custom');
    const component = condition('calculated_component_extra');

    const grouped = partitionPolicyEvidence([custom, component]);

    expect(grouped.eligibility).toEqual([custom, component]);
    expect(grouped.officialTerms).toEqual([]);
    expect(grouped.calculationEvidence).toEqual([]);
  });

  it('입력 배열을 변경하지 않고 빈 배열도 처리한다', () => {
    const original = [condition('age', 'met')];
    const snapshot = structuredClone(original);

    expect(partitionPolicyEvidence(original).eligibility).toEqual(original);
    expect(original).toEqual(snapshot);
    expect(partitionPolicyEvidence([])).toEqual({
      eligibility: [],
      officialTerms: [],
      calculationEvidence: [],
    });
  });
});
