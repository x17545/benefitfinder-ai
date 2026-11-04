// F09 이 기기에 기억하기. 서버가 아니라 이 브라우저(localStorage)에만 30일 저장한다.
import {
  ASSET_CHOICES,
  CONTRACT_CHOICES,
  DEPOSIT_CHOICES,
  EMPLOYMENT_CHOICES,
  HOME_OWNERSHIP_CHOICES,
  HOUSEHOLD_HEAD_CHOICES,
  HOUSEHOLD_SIZE_CHOICES,
  INCOME_CHOICES,
  INTEREST_CHOICES,
  LIVING_CHOICES,
  MARITAL_CHOICES,
  MILITARY_MONTHS,
  PARENT_ASSET_CHOICES,
  PARENT_INCOME_CHOICES,
  RECEIVED_CHOICES,
  RENT_CHOICES,
  SCHOOL_CHOICES,
  SPECIAL_CHOICES,
} from '../../domains/youth/conditionOptions.ts';
import type { Choice, MilitaryChoice } from '../../domains/youth/conditionOptions.ts';
import type { RecommendationRequest } from '../../shared/api/generated/recommendation-request.ts';
import { EMPTY_ANSWERS } from './flowStore.ts';
import type { ConditionAnswers } from './flowStore.ts';

export const SAVED_CONDITIONS_KEY = 'ypm.conditions.v1';
const MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000;

/** localStorage와 같은 모양. 테스트에서 가짜를 넣는다 */
export type KeyValueStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

export interface SavedConditions {
  savedAt: string;
  answers: ConditionAnswers;
}

function browserStorage(): KeyValueStorage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage;
  } catch {
    return null;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function record(value: unknown): Record<string, unknown> {
  return isRecord(value) ? value : {};
}

/** 약속된 선택지 값일 때만 받아들인다 */
function pick<T>(choices: readonly Choice<T>[], value: unknown): T | null {
  return choices.find((choice) => choice.value === value)?.value ?? null;
}

function pickAll<T>(choices: readonly Choice<T>[], value: unknown): T[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return choices.filter((choice) => value.includes(choice.value)).map((choice) => choice.value);
}

function pickMilitary(value: unknown): MilitaryChoice | null {
  const range = record(value);
  const entry = Object.entries(MILITARY_MONTHS).find(
    ([, months]) => months.min === range.min && months.max === range.max,
  );
  return entry !== undefined ? (entry[0] as MilitaryChoice) : null;
}

/** 저장해 둔 조건(서버 요청 형식) → S2 답. 모르는 값은 버린다 */
export function answersFromProfile(profile: unknown): ConditionAnswers | null {
  if (!isRecord(profile)) {
    return null;
  }
  const data = record(profile.domain_data);
  const housing = record(data.housing);
  const contract = record(data.contract);
  const household = record(data.household);
  const age = typeof profile.age === 'number' && Number.isInteger(profile.age) && profile.age >= 0 ? profile.age : null;

  const householdSize = pick(HOUSEHOLD_SIZE_CHOICES, household.household_size);
  const parentIncome = pick(PARENT_INCOME_CHOICES, household.parent_income_band);
  const parentAsset = pick(PARENT_ASSET_CHOICES, household.parent_asset_band);

  return {
    ...EMPTY_ANSWERS,
    age,
    military: data.military_months_range === null ? null : pickMilitary(data.military_months_range),
    sidoCode: typeof record(data.region).sido_code === 'string' ? String(record(data.region).sido_code) : null,
    regionCode: typeof profile.region_code === 'string' ? profile.region_code : null,
    employmentStatus: pick(EMPLOYMENT_CHOICES, profile.employment_status),
    school: pick(SCHOOL_CHOICES, data.school),
    marital: pick(MARITAL_CHOICES, data.marital),
    living: pick(LIVING_CHOICES, housing.living),
    homeOwnership: pick(HOME_OWNERSHIP_CHOICES, housing.home_ownership),
    householdHead: pick(HOUSEHOLD_HEAD_CHOICES, housing.household_head),
    contractType: pick(CONTRACT_CHOICES, contract.type),
    depositBand: pick(DEPOSIT_CHOICES, contract.deposit_band),
    rentBand: pick(RENT_CHOICES, contract.rent_band),
    incomeBand: pick(INCOME_CHOICES, profile.income_band),
    assetBand: pick(ASSET_CHOICES, data.asset_band),
    special: pickAll(SPECIAL_CHOICES, data.special),
    interests: pickAll(INTEREST_CHOICES, data.interests),
    received: pickAll(RECEIVED_CHOICES, data.received),
    householdSize,
    parentIncome,
    parentAsset,
    parentsSkipped: householdSize === null && parentIncome === null && parentAsset === null,
  };
}

/** 분석을 시작할 때 서버 요청 형식 그대로 저장한다 (F09-1, 2) */
export function saveConditions(
  profile: RecommendationRequest['profile'],
  storage: KeyValueStorage | null = browserStorage(),
  now: Date = new Date(),
): void {
  try {
    storage?.setItem(SAVED_CONDITIONS_KEY, JSON.stringify({ saved_at: now.toISOString(), conditions: profile }));
  } catch {
    // 저장 공간이 없으면 기억하지 않는다
  }
}

export function removeSavedConditions(storage: KeyValueStorage | null = browserStorage()): void {
  try {
    storage?.removeItem(SAVED_CONDITIONS_KEY);
  } catch {
    // 지울 수 없으면 그대로 둔다
  }
}

/** 저장된 조건을 읽는다. 30일이 지났거나 망가졌으면 지우고 null (F09-3) */
export function loadSavedConditions(
  storage: KeyValueStorage | null = browserStorage(),
  now: Date = new Date(),
): SavedConditions | null {
  let raw: string | null;
  try {
    raw = storage?.getItem(SAVED_CONDITIONS_KEY) ?? null;
  } catch {
    return null;
  }
  if (raw === null) {
    return null;
  }
  let saved: unknown;
  try {
    saved = JSON.parse(raw);
  } catch {
    saved = null;
  }
  const savedAt = isRecord(saved) && typeof saved.saved_at === 'string' ? Date.parse(saved.saved_at) : NaN;
  const answers = isRecord(saved) ? answersFromProfile(saved.conditions) : null;
  if (Number.isNaN(savedAt) || now.getTime() - savedAt > MAX_AGE_MS || answers === null) {
    removeSavedConditions(storage);
    return null;
  }
  return { savedAt: new Date(savedAt).toISOString(), answers };
}
