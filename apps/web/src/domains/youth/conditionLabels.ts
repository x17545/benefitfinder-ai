// 서버 판정의 condition_id → 화면에 보이는 조건 이름 (S3 진단 칩, S5 자격 진단)
const LABELS: Record<string, string> = {
  age: '나이',
  region: '지역',
  employment: '구직 상태',
  income: '소득',
  asset: '재산',
  home_ownership: '무주택',
  household_head: '세대주',
  monthly_rent: '월세 계약',
  deposit: '보증금',
  marital: '혼인',
  parent_income: '부모님 소득',
  parent_asset: '부모님 재산',
};

/** 모르는 조건은 서버가 보낸 이름을 그대로 보여 준다 */
export function conditionLabel(conditionId: string): string {
  return LABELS[conditionId] ?? conditionId;
}
