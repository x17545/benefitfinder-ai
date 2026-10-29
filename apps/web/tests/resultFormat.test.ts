import { describe, expect, it } from 'vitest';
import {
  formatManWon,
  isStale,
  josa,
  monthDay,
  recruitTag,
  shortPolicyId,
  withJosa,
} from '../src/features/benefits/format.ts';
import { allPolicyIds, pickView, portfolioLabel } from '../src/features/benefits/resultModel.ts';
import { isRecommendationResponse } from '../src/shared/api/recommendations.ts';
import fixtureText from '../../../contracts/fixtures/recommendation-response.json?raw';

describe('S3 formatting', () => {
  it('shows amounts in 만 원', () => {
    expect(formatManWon(5_400_000)).toBe('540만 원');
    expect(formatManWon(5_346_663)).toBe('약 535만 원');
    expect(formatManWon(120_000_000)).toBe('12,000만 원');
  });

  it('picks the right particle', () => {
    expect(withJosa('청년 월세 지원', '과', '와')).toBe('청년 월세 지원과');
    expect(withJosa('구직활동 지원금', '은', '는')).toBe('구직활동 지원금은');
    expect(withJosa('정책 P1', '은', '는')).toBe('정책 P1은(는)');
    expect(josa('대안', '이', '가')).toBe('이');
  });

  it('formats dates and ids', () => {
    expect(monthDay('2026-10-26T09:00:00+09:00')).toBe('10/26');
    expect(shortPolicyId('youth:example:P1')).toBe('P1');
    expect(isStale('2026-09-01T00:00:00+09:00', new Date('2026-10-29T00:00:00+09:00'))).toBe(true);
    expect(isStale('2026-10-26T09:00:00+09:00', new Date('2026-10-29T00:00:00+09:00'))).toBe(false);
  });

  it('builds the recruiting tag', () => {
    const now = new Date('2026-10-29T10:00:00+09:00');
    const open = { status: 'open', starts_on: null, ends_on: null, url: null } as const;
    expect(recruitTag(open, now)).toEqual({ label: '모집 중', tone: 'ok' });
    expect(recruitTag({ ...open, ends_on: '2026-11-09' }, now)).toEqual({ label: '마감 D-11', tone: 'check' });
    expect(recruitTag({ ...open, status: 'closed' }, now).label).toBe('모집 끝남');
    expect(recruitTag({ ...open, status: 'unknown' }, now).label).toBe('일정 확인 필요');
  });
});

describe('S3 result model', () => {
  const result: unknown = JSON.parse(fixtureText);

  it('reads the contract example', () => {
    if (!isRecommendationResponse(result)) {
      throw new Error('fixture does not match the contract');
    }
    expect(pickView(result, '2y', true).portfolios[0]?.total_won).toBe(5_869_626);
    expect(allPolicyIds(result)).toContain('youth:example:P6');
    expect(portfolioLabel(1)).toBe('추천 조합');
    expect(portfolioLabel(3)).toBe('대안 2');
  });
});
