/* Generated from contracts/v1. Run pnpm types:generate; do not edit. */

export type YouthDomainData = {
  cash_facts?: CashFacts;
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
 * 선택적 로컬/게시된 현금 정책 판정 입력. 기간 목록 누락/null은 미확인, 빈 배열은 해당하는 사업 참여 이력 없음. 모든 누락/null은 미상으로 보수적으로 처리하며 서버에 저장하거나 LLM·임베딩·GA·외부 서비스로 전달하지 않는다.
 */
export type CashFacts = {
  birth_date?: string | null;
  exam?: {
    exam_date?: string | null;
    fee_won?: LocalNullableWon;
    supported_exam?: boolean | null;
    unemployed_on_exam_date?: boolean | null;
    same_exam_awards?: number | null;
    prior_awards_won?: LocalNullableWon;
    other_reimbursement?: boolean | null;
    other_exclusions?: boolean | null;
  } | null;
  rent?: {
    deposit_won?: LocalNullableWon;
    contract_rent_won?: LocalNullableWon;
    health_insurance?: 'employee' | 'regional' | null;
    health_premium_won?: LocalNullableWon;
    applicant_lease?: boolean | null;
    other_exclusions?: boolean | null;
    /**
     * @maxItems 24
     */
    paid_months?:
      | {
          month: string;
          paid_on: string;
          amount_won: number;
        }[]
      | null;
  } | null;
  /**
   * 선택적 로컬/게시된 월세지원 판정 입력. 누락/null은 0, false, 미충족으로 바꾸지 않으며, 서버 저장과 LLM·임베딩·GA·외부 서비스 전송을 금지한다.
   */
  rent_support?: {
    /**
     * 공식 심사 기준중위소득 대비 청년가구 비율(bps). 10000=100%이며 개인 연봉 구간으로 추정하지 않는다.
     */
    youth_income_ratio_bps?: number | null;
    /**
     * 공식 심사 기준중위소득 대비 원가구 비율(bps). 10000=100%이며 개인 연봉 구간으로 추정하지 않는다.
     */
    original_income_ratio_bps?: number | null;
    youth_assets_won?: LocalNullableWon;
    original_assets_won?: LocalNullableWon;
    monthly_rent_won?: LocalNullableWon;
    housing_benefit_rent_won?: LocalNullableWon;
    /**
     * 나이·혼인 외 사유로 기관이 원가구 미고려를 인정했는지. 별도 사유 없음은 false, 미상은 null이다. 나이·혼인은 공통 age와 domain_data.marital을 쓴다.
     */
    original_household_exempt?: boolean | null;
    subscription_account?: boolean | null;
    independent_from_parents?: boolean | null;
    other_exclusions_clear?: boolean | null;
    requested_months?: number | null;
    prior_supported_months?: number | null;
    /**
     * 첫 지급예정월(YYYY-MM). 미상이면 계산을 확인 필요로 두며 미래예정월은 그 예정월로 판정한다. 게시 규칙의 실제 지급기간(현재 연결 대상 2024-03~2026-12)과 as_of 기준 상대 월 검사는 domain이 수행하고 요청기간을 임의로 바꾸지 않는다.
     */
    first_payment_month?: string | null;
  } | null;
  /**
   * @maxItems 50
   */
  program_periods?:
    | {
        program_id: string;
        starts_on: string;
        ends_on: string | null;
      }[]
    | null;
} | null;
export type LocalNullableWon = number | null;

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
