// S3 화면에서 쓰는 글자 만들기. 계산은 하지 않고 서버 값을 읽기 좋게만 바꾼다.
import type { NormalizedBenefit } from '../../shared/api/benefits.ts';

export type Period = '1y' | '2y' | 'all';

export const PERIOD_TABS: readonly { value: Period; label: string; short: string }[] = [
  { value: '1y', label: '1년 기준', short: '1년' },
  { value: '2y', label: '2년 기준', short: '2년' },
  { value: 'all', label: '전체 기간 기준', short: '전체 기간' },
];

/** 5,400,000 → "540만 원", 5,346,663 → "약 535만 원" */
export function formatManWon(won: number): string {
  const man = won / 10_000;
  if (Number.isInteger(man)) {
    return `${man.toLocaleString('ko-KR')}만 원`;
  }
  return `약 ${Math.round(man).toLocaleString('ko-KR')}만 원`;
}

/** "youth:example:P1" → "P1" (이름을 아직 모를 때만 쓴다) */
export function shortPolicyId(id: string): string {
  const parts = id.split(':');
  return parts[parts.length - 1] ?? id;
}

/** 한글 끝 글자에 받침이 있으면 true. 한글이 아니면 null */
function hasFinalConsonant(word: string): boolean | null {
  const code = word.charCodeAt(word.length - 1);
  if (code < 0xac00 || code > 0xd7a3) {
    return null;
  }
  return (code - 0xac00) % 28 !== 0;
}

/** 받침에 맞는 조사만. josa('청년 월세 지원', '과', '와') → "과" */
export function josa(word: string, withFinal: string, withoutFinal: string): string {
  const final = hasFinalConsonant(word);
  if (final === null) {
    return `${withFinal}(${withoutFinal})`;
  }
  return final ? withFinal : withoutFinal;
}

/** 조사 붙이기. withJosa('청년 월세 지원', '과', '와') → "청년 월세 지원과" */
export function withJosa(word: string, withFinal: string, withoutFinal: string): string {
  return `${word}${josa(word, withFinal, withoutFinal)}`;
}

/** 출처 주소 → 이름. 예: youthcenter.go.kr → 온통청년 */
export function sourceName(url: string): string {
  let host: string;
  try {
    host = new URL(url).hostname;
  } catch {
    return url;
  }
  if (host.endsWith('youthcenter.go.kr')) {
    return '온통청년';
  }
  if (host.endsWith('gov.kr')) {
    return '보조금24';
  }
  if (host.endsWith('bokjiro.go.kr')) {
    return '복지로';
  }
  return host;
}

/** "2026-10-26T09:00:00+09:00" → "10/26" (한국 시간) */
export function monthDay(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return iso;
  }
  const kst = new Date(date.getTime() + 9 * 60 * 60 * 1000);
  return `${String(kst.getUTCMonth() + 1)}/${String(kst.getUTCDate())}`;
}

/** 확인한 지 30일이 넘었으면 true (문구 S3-OLD) */
export function isStale(iso: string, now: Date = new Date()): boolean {
  const time = Date.parse(iso);
  return !Number.isNaN(time) && now.getTime() - time > 30 * 24 * 60 * 60 * 1000;
}

export interface RecruitTag {
  label: string;
  tone: 'ok' | 'check' | 'muted';
}

/** 모집 상태 태그: 모집 중 / 마감 D-n / 모집 예정 / 모집 끝남 / 일정 확인 필요 */
export function recruitTag(application: NormalizedBenefit['application'], now: Date = new Date()): RecruitTag {
  if (application.status === 'closed') {
    return { label: '모집 끝남', tone: 'muted' };
  }
  if (application.status === 'upcoming') {
    return { label: '모집 예정', tone: 'muted' };
  }
  if (application.status === 'unknown') {
    return { label: '일정 확인 필요', tone: 'check' };
  }
  if (application.ends_on !== null) {
    // 한국 날짜 기준으로 오늘부터 마감일까지 남은 날 수
    const kst = new Date(now.getTime() + 9 * 60 * 60 * 1000);
    const today = Date.UTC(kst.getUTCFullYear(), kst.getUTCMonth(), kst.getUTCDate());
    const days = Math.round((Date.parse(`${application.ends_on}T00:00:00Z`) - today) / 86_400_000);
    if (days >= 0 && days <= 30) {
      return { label: `마감 D-${String(days)}`, tone: 'check' };
    }
  }
  return { label: '모집 중', tone: 'ok' };
}
