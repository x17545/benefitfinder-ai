/* Generated from contracts/v1. Run pnpm types:generate; do not edit. */

/**
 * POST /api/v1/recommendations 요청. 13단계 선택지 단일 계약이며 문장 입력 필드는 없다. domain/profile/options가 원본이다.
 */
export type RecommendationRequest = {
  domain: string;
  profile: UserConfirmedProfile;
  /**
   * 기간+현재가치 공통 옵션(단일 위치). 서버는 응답에 3기간×PV on/off 6가지를 모두 담는다.
   */
  options?: {
    period?: '1y' | '2y' | 'all';
    present_value?: boolean;
  } | null;
};

export interface UserConfirmedProfile {
  age?: number | null;
  region_code?: string | null;
  employment_status?: string | null;
  income_band?: string | null;
  household_type?: string | null;
  domain_data?: {
    [k: string]: unknown;
  };
}
