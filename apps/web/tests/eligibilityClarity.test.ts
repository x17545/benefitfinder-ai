import { describe, expect, it } from 'vitest';
import {
  eligibilityDisplayStatus,
  portfolioEligibilityStatus,
} from '../src/features/benefits/resultModel.ts';

describe('지원금 자격 상태 표시', () => {
  it('모든 조건이 충족됐을 때만 met으로 표시한다', () => {
    expect(eligibilityDisplayStatus([{ status: 'met' }, { status: 'met' }])).toBe('met');
  });

  it('하나라도 확인 필요이면 needs_review로 표시한다', () => {
    expect(eligibilityDisplayStatus([{ status: 'met' }, { status: 'needs_review' }])).toBe('needs_review');
    expect(eligibilityDisplayStatus(Array.from({ length: 11 }, () => ({ status: 'needs_review' })))).toBe('needs_review');
  });

  it('미충족은 확인 필요보다 우선한다', () => {
    expect(eligibilityDisplayStatus([{ status: 'needs_review' }, { status: 'not_met' }])).toBe('not_met');
  });

  it('진단이 없거나 비어 있으면 충족으로 판단하지 않는다', () => {
    expect(eligibilityDisplayStatus(undefined)).toBe('unknown');
    expect(eligibilityDisplayStatus(null)).toBe('unknown');
    expect(eligibilityDisplayStatus([])).toBe('unknown');
  });

  it('조합에 확인 필요 정책이 있으면 조합도 확인 필요로 표시한다', () => {
    expect(portfolioEligibilityStatus([
      { eligibility: [{ status: 'met' }] },
      { eligibility: [{ status: 'needs_review' }] },
    ])).toBe('needs_review');
  });

  it('조합의 미충족과 진단 없음을 안전하게 처리한다', () => {
    expect(portfolioEligibilityStatus([
      { eligibility: [{ status: 'met' }] },
      { eligibility: [{ status: 'not_met' }] },
    ])).toBe('not_met');
    expect(portfolioEligibilityStatus([{ eligibility: [] }])).toBe('unknown');
    expect(portfolioEligibilityStatus([])).toBe('unknown');
  });
});
