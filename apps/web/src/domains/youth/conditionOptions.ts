// S2 조건 고르기 13단계의 선택지 (기획서 4.3 단계별 질문).
// value는 서버와 약속한 값(contracts/v1 youth-profile)을 그대로 쓰고, label은 화면 문구다.
import type { YouthProfileExtension } from '../../shared/api/generated/youth-profile.ts';

type YouthData = NonNullable<YouthProfileExtension['domain_data']>;
type Housing = NonNullable<YouthData['housing']>;
type Contract = NonNullable<YouthData['contract']>;
type Household = NonNullable<YouthData['household']>;

export type EmploymentStatus = NonNullable<YouthProfileExtension['employment_status']>;
export type IncomeBand = NonNullable<YouthProfileExtension['income_band']>;
export type School = NonNullable<YouthData['school']>;
export type Marital = NonNullable<YouthData['marital']>;
export type Living = NonNullable<Housing['living']>;
export type HomeOwnership = NonNullable<Housing['home_ownership']>;
export type HouseholdHead = NonNullable<Housing['household_head']>;
export type ContractType = NonNullable<Contract['type']>;
export type DepositBand = NonNullable<Contract['deposit_band']>;
export type RentBand = NonNullable<Contract['rent_band']>;
export type AssetBand = NonNullable<YouthData['asset_band']>;
export type Special = NonNullable<YouthData['special']>[number];
export type Interest = NonNullable<YouthData['interests']>[number];
export type Received = NonNullable<YouthData['received']>[number];
export type HouseholdSize = NonNullable<Household['household_size']>;
export type ParentIncomeBand = NonNullable<Household['parent_income_band']>;
export type ParentAssetBand = NonNullable<Household['parent_asset_band']>;

/** 화면에서 '모름'을 고른 것. 서버에는 null(미상)로 보낸다 */
export type Unknown = 'unknown';

export interface Choice<T> {
  value: T;
  label: string;
}

/* 1단계 나이 */
export const AGE_NOTICE_MIN = 19;
export const AGE_NOTICE_MAX = 39;
/** 이 나이 이상이면 군 복무 질문이 나온다 */
export const MILITARY_QUESTION_MIN_AGE = 35;

export type MilitaryChoice = 'none' | 'up_to_18' | 'from_19_to_24' | 'over_24';

export const MILITARY_CHOICES: readonly Choice<MilitaryChoice>[] = [
  { value: 'none', label: '해당 없음' },
  { value: 'up_to_18', label: '1년 6개월 이하' },
  { value: 'from_19_to_24', label: '1년 6개월 초과 ~ 2년' },
  { value: 'over_24', label: '2년 초과' },
];

/** 군 복무 칩 → 서버로 보내는 개월 구간 (contracts/v1 military_months_range) */
export const MILITARY_MONTHS: Record<MilitaryChoice, { min: number | null; max: number | null }> = {
  none: { min: 0, max: 0 },
  up_to_18: { min: 0, max: 18 },
  from_19_to_24: { min: 19, max: 24 },
  over_24: { min: 25, max: null },
};

/* 3단계 지금 상황 */
export const EMPLOYMENT_CHOICES: readonly Choice<EmploymentStatus>[] = [
  { value: 'high_school', label: '고등학생' },
  { value: 'student', label: '대학생 (휴학 포함)' },
  { value: 'grad_student', label: '대학원생' },
  { value: 'job_seeker', label: '취업 준비' },
  { value: 'employed', label: '재직 (정규·계약)' },
  { value: 'short_term_worker', label: '단기·일용 근로' },
  { value: 'self_employed', label: '자영업' },
  { value: 'freelancer', label: '프리랜서' },
  { value: 'founder', label: '(예비) 창업' },
  { value: 'farmer', label: '농업·어업' },
  { value: 'other', label: '기타' },
];

/* 4단계 최종 학력 */
export const SCHOOL_CHOICES: readonly Choice<School>[] = [
  { value: 'high_school_below', label: '고졸 미만' },
  { value: 'high_school_enrolled', label: '고등학교 재학' },
  { value: 'high_school_graduate', label: '고등학교 졸업' },
  { value: 'university_enrolled', label: '대학 재학·휴학' },
  { value: 'university_expected', label: '대학 졸업 예정' },
  { value: 'university_graduate', label: '대학 졸업' },
  { value: 'masters_doctorate', label: '석·박사' },
];

/* 5단계 결혼 */
export const MARITAL_CHOICES: readonly Choice<Marital>[] = [
  { value: 'single', label: '미혼' },
  { value: 'married', label: '기혼' },
  { value: 'marrying_soon', label: '결혼 예정 (3개월 안)' },
];

/* 6단계 사는 형태 */
export const LIVING_CHOICES: readonly Choice<Living>[] = [
  { value: 'alone', label: '혼자' },
  { value: 'with_parents', label: '부모님과 함께' },
  { value: 'with_family', label: '배우자·가족과' },
  { value: 'dorm', label: '기숙사·고시원' },
  { value: 'other', label: '기타' },
];

export const HOME_OWNERSHIP_CHOICES: readonly Choice<HomeOwnership>[] = [
  { value: 'homeless', label: '없어요 (무주택)' },
  { value: 'owned', label: '있어요' },
  { value: 'unknown', label: '모름' },
];

export const HOUSEHOLD_HEAD_CHOICES: readonly Choice<HouseholdHead>[] = [
  { value: 'head', label: '세대주' },
  { value: 'member', label: '세대원' },
  { value: 'unknown', label: '모름' },
];

/* 7단계 집 계약 */
export const CONTRACT_CHOICES: readonly Choice<ContractType>[] = [
  { value: 'monthly_rent', label: '월세' },
  { value: 'jeonse', label: '전세' },
  { value: 'owned', label: '자가' },
  { value: 'none', label: '계약 없음' },
];

export const DEPOSIT_CHOICES: readonly Choice<DepositBand>[] = [
  { value: 'under_50m', label: '5천만 원 이하' },
  { value: 'between_50m_100m', label: '5천만 ~ 1억' },
  { value: 'between_100m_300m', label: '1억 ~ 3억' },
  { value: 'over_300m', label: '3억 초과' },
];

export const RENT_CHOICES: readonly Choice<RentBand>[] = [
  { value: 'under_40', label: '40만 원 이하' },
  { value: 'between_40_60', label: '40만 ~ 60만' },
  { value: 'between_60_70', label: '60만 ~ 70만' },
  { value: 'over_70', label: '70만 초과' },
];

/* 8단계 소득·재산 */
export const INCOME_CHOICES: readonly Choice<IncomeBand>[] = [
  { value: 'none', label: '없음' },
  { value: 'under_15m', label: '1,500만 미만' },
  { value: 'between_15m_30m', label: '1,500만 ~ 3,000만' },
  { value: 'between_30m_50m', label: '3,000만 ~ 5,000만' },
  { value: 'over_50m', label: '5,000만 이상' },
  { value: 'unknown', label: '모름' },
];

export const ASSET_CHOICES: readonly Choice<AssetBand>[] = [
  { value: 'under_122m', label: '1억 2천만 이하' },
  { value: 'between_122m_345m', label: '1억 2천만 ~ 3억 4,500만' },
  { value: 'over_345m', label: '3억 4,500만 초과' },
  { value: 'unknown', label: '모름' },
];

/* 9단계 해당 조건 (민감 정보: LLM·GA·로그로 보내지 않는다) */
export const SPECIAL_CHOICES: readonly Choice<Special>[] = [
  { value: 'basic_livelihood', label: '기초생활수급자' },
  { value: 'near_poverty', label: '차상위계층' },
  { value: 'single_parent', label: '한부모가정' },
  { value: 'disabled', label: '장애인' },
  { value: 'sme_employee', label: '중소기업 재직' },
  { value: 'farmer_fisher', label: '농업인·어업인' },
  { value: 'soldier', label: '군 복무 중' },
  { value: 'none', label: '해당 없음' },
];

/* 10단계 관심 지원 (첫 MVP는 취업·주거만) */
export const INTEREST_CHOICES: readonly Choice<Interest>[] = [
  { value: 'employment', label: '취업' },
  { value: 'housing', label: '주거' },
];

/* 11단계 받았던 지원 */
export const RECEIVED_CHOICES: readonly Choice<Received>[] = [
  { value: 'monthly_rent', label: '청년월세 지원' },
  { value: 'youth_account', label: '청년도약계좌·청년미래적금' },
  { value: 'tomorrow_account', label: '청년내일저축계좌' },
  { value: 'employment_support', label: '국민취업지원제도' },
  { value: 'jeonse_loan', label: '버팀목 전세대출' },
  { value: 'none', label: '없음' },
];

/* 12단계 부모님 정보 (선택, 민감 정보) */
export const HOUSEHOLD_SIZE_CHOICES: readonly Choice<HouseholdSize | Unknown>[] = [
  { value: 1, label: '1명' },
  { value: 2, label: '2명' },
  { value: 3, label: '3명' },
  { value: 4, label: '4명 이상' },
  { value: 'unknown', label: '모름' },
];

export const PARENT_INCOME_CHOICES: readonly Choice<ParentIncomeBand | Unknown>[] = [
  { value: 'under_3m', label: '300만 이하' },
  { value: 'between_3m_5m', label: '300만 ~ 500만' },
  { value: 'between_5m_7m', label: '500만 ~ 700만' },
  { value: 'over_7m', label: '700만 초과' },
  { value: 'unknown', label: '모름' },
];

export const PARENT_ASSET_CHOICES: readonly Choice<ParentAssetBand | Unknown>[] = [
  { value: 'under_300m', label: '3억 이하' },
  { value: 'between_300m_500m', label: '3억 ~ 5억' },
  { value: 'over_500m', label: '5억 초과' },
  { value: 'unknown', label: '모름' },
];

/** 단계별 분류 이름 (질문 위 작은 글씨) */
export const STEP_CATEGORIES: Record<number, string> = {
  1: '기본 정보',
  2: '기본 정보',
  3: '지금 상황',
  4: '지금 상황',
  5: '지금 상황',
  6: '사는 곳 · 소득',
  7: '사는 곳 · 소득',
  8: '사는 곳 · 소득',
  9: '해당 조건 · 관심',
  10: '해당 조건 · 관심',
  11: '해당 조건 · 관심',
  12: '부모님 정보',
  13: '확인',
};
