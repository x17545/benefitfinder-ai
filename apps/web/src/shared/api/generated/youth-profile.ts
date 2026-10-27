/* Generated from contracts/v1. Run pnpm types:generate; do not edit. */

export type YouthDomainData = {
  /**
   * 군 복무 개월 구간. 정확한 개월은 min==max, 미상은 전체 null, 열린 상한은 max:null. UI 매핑: 해당없음 0..0, 1년6개월이하 0..18, 1년6개월초과~2년 19..24, 2년초과 25..null. min<=max는 스키마가 강제하지 않고 문서/테스트에서 검증한다. 정책별 최대 6년 연장은 해당 공고 근거가 있을 때만 적용하고 근거 없으면 사용하지 않는다.
   */
  military_months_range?: {
    min: number | null;
    max: number | null;
  } | null;
  /**
   * 피커 상위 시도 코드. 시군구 전체 코드는 공통 region_code가 SSOT이며 여기 두지 않는다.
   */
  region?: {
    sido_code: string;
  } | null;
  school?:
    | 'high_school_below'
    | 'high_school_enrolled'
    | 'high_school_graduate'
    | 'university_enrolled'
    | 'university_expected'
    | 'university_graduate'
    | 'masters_doctorate'
    | null;
  marital?: 'single' | 'married' | 'marrying_soon' | null;
  housing?: {
    living?: 'alone' | 'with_parents' | 'with_family' | 'dorm' | 'other' | null;
    home_ownership?: 'homeless' | 'owned' | 'unknown' | null;
    household_head?: 'head' | 'member' | 'unknown' | null;
  } | null;
  contract?: {
    type?: 'monthly_rent' | 'jeonse' | 'owned' | 'none' | null;
    deposit_band?: 'under_50m' | 'between_50m_100m' | 'between_100m_300m' | 'over_300m' | null;
    rent_band?: 'under_40' | 'between_40_60' | 'between_60_70' | 'over_70' | null;
  } | null;
  asset_band?: 'under_122m' | 'between_122m_345m' | 'over_345m' | 'unknown' | null;
  /**
   * 민감 특수조건. LLM/임베딩/GA/로그 전송 금지. none은 단독으로만 사용한다(스키마 강제 아님, 문서/테스트 검증).
   */
  special?:
    | (
        | 'basic_livelihood'
        | 'near_poverty'
        | 'single_parent'
        | 'disabled'
        | 'sme_employee'
        | 'farmer_fisher'
        | 'soldier'
        | 'none'
      )[]
    | null;
  interests?: ('housing' | 'employment')[] | null;
  received?:
    ('monthly_rent' | 'youth_account' | 'tomorrow_account' | 'employment_support' | 'jeonse_loan' | 'none')[] | null;
  household?: {
    /**
     * 4는 4명 이상.
     */
    household_size?: 1 | 2 | 3 | 4 | null;
    /**
     * 민감 부모정보. LLM/임베딩/GA/로그 전송 금지. 기혼이면 null.
     */
    parent_income_band?: 'under_3m' | 'between_3m_5m' | 'between_5m_7m' | 'over_7m' | null;
    /**
     * 민감 부모정보. LLM/임베딩/GA/로그 전송 금지. 기혼이면 null.
     */
    parent_asset_band?: 'under_300m' | 'between_300m_500m' | 'over_500m' | null;
  } | null;
} | null;

/**
 * domain==youth일 때만 적용되는 13단계 선택지 확장. 공통 profile의 age/region_code/employment_status/income_band/household_type은 유지하고 나머지 선택지는 domain_data에 둔다. 기간+현재가치 옵션은 request.options 단일 위치에 두며 domain_data에는 두지 않는다. null/누락은 미상이며 없는 것으로 단정하지 않는다. min<=max 같은 교차 필드 불변식은 JSON Schema가 아니라 문서와 테스트로 검증한다.
 */
export interface YouthProfileExtension {
  /**
   * 만 나이. 19~39세 밖이어도 막지 않고 안내만 한다.
   */
  age?: number | null;
  /**
   * 지역 SSOT. 시군구 행정표준코드 전체(예: 41110). 미상은 null.
   */
  region_code?: string | null;
  /**
   * S2-3 지금 상황. 온통청년 jobCd 대응.
   */
  employment_status?:
    | 'high_school'
    | 'student'
    | 'grad_student'
    | 'job_seeker'
    | 'employed'
    | 'short_term_worker'
    | 'self_employed'
    | 'freelancer'
    | 'founder'
    | 'farmer'
    | 'other'
    | null;
  /**
   * 본인 연 소득(세전) 구간. 모름은 unknown 또는 null(미상).
   */
  income_band?: 'none' | 'under_15m' | 'between_15m_30m' | 'between_30m_50m' | 'over_50m' | 'unknown' | null;
  /**
   * 공통 호환용. 청년 확장은 domain_data.housing/living을 사용하고 여기에는 값을 추정해 채우지 않는다.
   */
  household_type?: string | null;
  domain_data?: YouthDomainData;
}
