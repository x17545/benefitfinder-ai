import { describe, expect, it } from 'vitest';
import { imageFileName, imageFooter } from '../src/features/benefits/resultImage.ts';

describe('result image (F10)', () => {
  it('names the file and footer by the Korean date', () => {
    // 한국 시간 2026-10-26 00:30 = UTC 2026-10-25 15:30
    const date = new Date('2026-10-25T15:30:00Z');
    expect(imageFileName(date)).toBe('청년정책매니저_조합_20261026.png');
    expect(imageFooter(date)).toBe('청년 정책 매니저 · AI 참고 결과 · 생성일 2026.10.26');
  });
});
