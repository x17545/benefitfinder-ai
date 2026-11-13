import { describe, expect, it } from 'vitest';
import { amountBreakdownText } from '../src/features/benefits/format.ts';
import {
  imageFileName,
  imageFooter,
  resultImageAmountBasisNote,
} from '../src/features/benefits/resultImage.ts';

describe('result image (F10)', () => {
  it('names the file and footer by the Korean date', () => {
    // 한국 시간 2026-10-26 00:30 = UTC 2026-10-25 15:30
    const date = new Date('2026-10-25T15:30:00Z');
    expect(imageFileName(date)).toBe('청년정책매니저_조합_20261026.png');
    expect(imageFooter(date)).toBe('청년 정책 매니저 · 참고 결과 · 생성일 2026.10.26');
    expect(imageFooter(date, true)).toContain('가상 정책 예제 · 실제 지원금 아님');
  });

  it('keeps cash and expected accrual separate in the image text', () => {
    expect(amountBreakdownText({ cash_in_period_won: 1_200_000, expected_accrual_won: 3_000_000 }))
      .toBe('현금 지급 예정(할인 전) 120만 원 · 만기 전 예상 적립 300만 원');
  });

  it('records the maturity and present-value basis in the image model', () => {
    expect(resultImageAmountBasisNote(false, false)).toBeUndefined();
    expect(resultImageAmountBasisNote(true, false)).toBeUndefined();
    expect(resultImageAmountBasisNote(false, true)).toContain('명목 합계에는 만기 전 예상 적립액이 포함');
    expect(resultImageAmountBasisNote(true, true)).toContain('기간 내 지급 예정 현금만 반영');
    expect(resultImageAmountBasisNote(true, true)).toContain('만기 전 예상 적립액은 제외');
  });
});
