// 서버 판정의 condition_id → 화면에 보이는 조건 이름 (S3 진단 칩, S5 자격 진단, S6 서비스)
// 실제 수집 정책은 `selection_region_code`, `pending:zipCd`, `youth-audience-manual`처럼
// 내부 식별자를 그대로 보내므로, 화면에는 아는 이름으로 바꾸고 모르는 것은 '기타 조건'으로 보인다.
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
  location: '지역',
  residence: '지역',
  school: '학력',
  citizenship: '국적',
  language: '언어',
  preference: '우대 조건',
  calculated_component: '금액 계산 기준',
};

/** 온통청년 원문 항목 이름 (pending:·source: 조건, 근거 위치 fields.*) */
const SOURCE_FIELD_LABELS: Record<string, string> = {
  plcyExplnCn: '정책 설명',
  plcySprtCn: '지원 내용',
  plcyAplyMthdCn: '신청 방법',
  aplyPrdSeCd: '신청 기간',
  addAplyQlfcCndCn: '추가 신청 자격',
  ptcpPrpTrgtCn: '참여 제한 대상',
  etcMttrCn: '기타 사항',
  srngMthdCn: '선정 방법',
  sbmsnDcmntCn: '제출 서류',
  earnMinAmt: '소득',
  earnMaxAmt: '소득',
  earnCndSeCd: '소득',
  earnEtcCn: '소득',
  schoolCd: '학력',
  jobCd: '구직 상태',
  sprtTrgtAgeLmtYn: '나이',
  sprtTrgtMinAge: '나이',
  sprtTrgtMaxAge: '나이',
  zipCd: '지역',
  mrgSttsCd: '혼인',
  sbizCd: '특화 조건',
  plcyMajorCd: '전공',
  plcyPvsnMthdCd: '지원 방식',
  bizPrdSeCd: '사업 기간',
  bizPrdEtcCn: '사업 기간',
  sprtArvlSeqYn: '선착순 여부',
  sprtSclLmtYn: '지원 인원',
  sprtSclCnt: '지원 인원',
};

/** 이름 일부로 알아볼 수 있는 조건. 위에서부터 먼저 맞는 것을 쓴다 */
const PATTERNS: ReadonlyArray<readonly [RegExp, string]> = [
  [/language/, '언어'],
  [/nationality|citizenship/, '국적'],
  [/(^|[^a-z])age([^a-z]|$)/, '나이'],
  [/region|residence|location|sido|address/, '지역'],
  [/school|education|degree/, '학력'],
  [/employment|job|unemploy/, '구직 상태'],
  [/marital|marri/, '혼인'],
  [/parent/, '부모님 정보'],
  [/income|earn/, '소득'],
  [/asset/, '재산'],
  [/qualification|license|certif/, '자격 요건'],
  // rental(정장 대여 등)은 주거가 아니므로 rent는 낱말 단위로만 본다
  [/housing|home|deposit|(^|[^a-z])rent([^a-z]|$)/, '주거 조건'],
  [/preference/, '우대 조건'],
  [/exclusion|duplicat|previous|overlap/, '참여 제한'],
  [/audience|target|intern|youth/, '지원 대상'],
];

const OTHER = '기타 조건';

/** 화면에 보일 조건 이름. 내부 식별자를 그대로 보이지 않는다 */
export function conditionLabel(conditionId: string): string {
  const known = LABELS[conditionId];
  if (known !== undefined) {
    return known;
  }
  // pending:zipCd, source:addAplyQlfcCndCn:2 처럼 원문 항목에서 온 조건
  const field = /^(?:pending|source):([A-Za-z]+)/.exec(conditionId);
  if (field !== null) {
    return SOURCE_FIELD_LABELS[field[1]] ?? OTHER;
  }
  if (/^official_source_terms/.test(conditionId)) {
    return '공고 조건';
  }
  const id = conditionId
    .replace(/^selection_(domain_data_)?/, '')
    .toLowerCase();
  const direct = LABELS[id];
  if (direct !== undefined) {
    return direct;
  }
  for (const [pattern, label] of PATTERNS) {
    if (pattern.test(id)) {
      return label;
    }
  }
  return OTHER;
}

/** 같은 이름이 여러 번 나오면 한 번만 (예: region · location → 지역) */
export function uniqueConditionLabels(conditionIds: readonly string[]): string[] {
  return [...new Set(conditionIds.map(conditionLabel))];
}

/**
 * 원문 인용의 위치 표시. `fields.srngMthdCn`은 항목 이름으로 바꾸고,
 * `chars:14018-14085`처럼 사람이 읽을 수 없는 위치는 숨긴다(null).
 */
export function evidenceLocationLabel(location: string | null | undefined): string | null {
  if (location === null || location === undefined) {
    return null;
  }
  const text = location.trim();
  if (text === '') {
    return null;
  }
  const field = /^fields\.([A-Za-z]+)/.exec(text);
  if (field !== null) {
    return SOURCE_FIELD_LABELS[field[1]] ?? null;
  }
  // chars:0-10, page:3, row_2 같은 기계용 위치
  if (/^[A-Za-z_]+[:.\-_]/.test(text) || /^[\x21-\x7e]+$/.test(text)) {
    return null;
  }
  return text;
}

/** 서버 판정 사유에서 내부 판정 값("내부 규칙 판정: met" 등)을 뺀다 */
export function readableReason(reason: string | null | undefined): string {
  if (reason === null || reason === undefined) {
    return '';
  }
  return reason
    .replace(/\s*\(?내부 규칙 판정\s*:\s*[A-Za-z_]+\)?\.?/g, '')
    .replace(/\s{2,}/g, ' ')
    .trim();
}

type ConditionStatus = 'met' | 'not_met' | 'needs_review';

/** 미충족 → 확인 필요 → 충족 순서. 사용자가 먼저 봐야 할 것을 앞에 둔다 */
const STATUS_ORDER: Record<ConditionStatus, number> = { not_met: 0, needs_review: 1, met: 2 };

/** 판정을 상태 순서로 정렬한다. 같은 상태끼리는 원래 순서를 지킨다 */
export function sortByStatus<T extends { status: ConditionStatus }>(items: readonly T[]): T[] {
  return items
    .map((item, index) => ({ item, index }))
    .sort((a, b) => STATUS_ORDER[a.item.status] - STATUS_ORDER[b.item.status] || a.index - b.index)
    .map(({ item }) => item);
}

export interface ConditionChip<S extends ConditionStatus> {
  key: string;
  label: string;
  status: S;
}

/** 칩 목록: 같은 이름·같은 상태는 하나로 합치고 상태 순서로 정렬한다 */
export function conditionChips<S extends ConditionStatus>(
  items: readonly { condition_id: string; status: S }[],
): ConditionChip<S>[] {
  const chips = new Map<string, ConditionChip<S>>();
  for (const item of items) {
    const label = conditionLabel(item.condition_id);
    const key = `${item.status}:${label}`;
    if (!chips.has(key)) {
      chips.set(key, { key, label, status: item.status });
    }
  }
  return sortByStatus([...chips.values()]);
}
