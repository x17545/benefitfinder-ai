/* Generated from contracts/v1. Run pnpm types:generate; do not edit. */

/**
 * POST /api/v1/simulations 요청. profile은 변경 전 조건, scenario는 S4 상황 칩. 기간+현재가치 옵션은 recommendation 요청과 동일한 options 단일 위치에 둔다. income_up처럼 소득이 바뀌는 경우 income_change로 연간 소득 대표값을 함께 보낼 수 있다.
 */
export type ChangeSimulationRequest = {
  domain: string;
  profile: UserConfirmedProfile;
  scenario: 'employed' | 'income_up' | 'independent' | 'age_plus_one';
  /**
   * 기간+현재가치 공통 옵션(단일 위치, recommendation과 동일).
   */
  options?: {
    period?: '1y' | '2y' | 'all';
    present_value?: boolean;
  } | null;
  /**
   * 연간 소득 변화(annual).
   */
  income_change?: {
    /**
     * 연간(annual) 소득.
     */
    from_won: number;
    /**
     * 연간(annual) 소득.
     */
    to_won: number;
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
