// 결과·서비스 카드의 신청 바로가기. 상세 화면(S5)과 같은 기준을 쓴다.
// 모집 중이고 공식 신청 주소가 있을 때만 [신청하러 가기], 그 밖에는 출처 [공식 페이지 보기].
import type { Application } from '../../shared/api/generated/recommendation-response.ts';
import { recruitmentState } from './format.ts';

export interface ApplyLink {
  url: string;
  label: '신청하러 가기 ↗' | '공식 페이지 보기 ↗';
  /** 실제 신청 주소로 가는지 (apply_click 기록 대상) */
  isApplication: boolean;
}

/** 가상 정책이거나 정보가 없으면 null (링크를 추측하지 않는다) */
export function applyLink(
  card: { application: Application; source: { url: string }; virtual_data?: boolean } | undefined,
  virtualData: boolean,
  now: Date = new Date(),
): ApplyLink | null {
  if (card === undefined || virtualData || card.virtual_data === true) {
    return null;
  }
  const applicationUrl = card.application.url;
  if (recruitmentState(card.application, now) === 'open' && applicationUrl !== null) {
    return { url: applicationUrl, label: '신청하러 가기 ↗', isApplication: true };
  }
  return { url: card.source.url, label: '공식 페이지 보기 ↗', isApplication: false };
}
