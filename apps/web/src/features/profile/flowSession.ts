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
  MILITARY_CHOICES,
  PARENT_ASSET_CHOICES,
  PARENT_INCOME_CHOICES,
  RECEIVED_CHOICES,
  RENT_CHOICES,
  SCHOOL_CHOICES,
  SPECIAL_CHOICES,
} from '../../domains/youth/conditionOptions.ts';
import type { Choice } from '../../domains/youth/conditionOptions.ts';
import type { ConditionAnswers, FlowState } from './flowStore.ts';

/** 입력 중인 값과 동의. 탭을 닫으면 사라지며 30일 기억하기와 구분한다. */
export const FLOW_SESSION_KEY = 'ypm.flow.v1';

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isNullableString(value: unknown): boolean {
  return value === null || typeof value === 'string';
}

function isChoice<T>(choices: readonly Choice<T>[], value: unknown): boolean {
  return value === null || choices.some((choice) => choice.value === value);
}

function isChoices<T>(choices: readonly Choice<T>[], value: unknown): boolean {
  return Array.isArray(value) && value.every((item: unknown) => choices.some((choice) => choice.value === item));
}

function isAnswers(value: unknown): value is ConditionAnswers {
  if (!isRecord(value)) {
    return false;
  }
  return (
    (value.age === null || (typeof value.age === 'number' && Number.isInteger(value.age) && value.age >= 0)) &&
    isChoice(MILITARY_CHOICES, value.military) &&
    isNullableString(value.sidoCode) &&
    isNullableString(value.regionCode) &&
    isNullableString(value.regionLabel) &&
    isChoice(EMPLOYMENT_CHOICES, value.employmentStatus) &&
    isChoice(SCHOOL_CHOICES, value.school) &&
    isChoice(MARITAL_CHOICES, value.marital) &&
    isChoice(LIVING_CHOICES, value.living) &&
    isChoice(HOME_OWNERSHIP_CHOICES, value.homeOwnership) &&
    isChoice(HOUSEHOLD_HEAD_CHOICES, value.householdHead) &&
    isChoice(CONTRACT_CHOICES, value.contractType) &&
    isChoice(DEPOSIT_CHOICES, value.depositBand) &&
    isChoice(RENT_CHOICES, value.rentBand) &&
    isChoice(INCOME_CHOICES, value.incomeBand) &&
    isChoice(ASSET_CHOICES, value.assetBand) &&
    isChoices(SPECIAL_CHOICES, value.special) &&
    isChoices(INTEREST_CHOICES, value.interests) &&
    isChoices(RECEIVED_CHOICES, value.received) &&
    isChoice(HOUSEHOLD_SIZE_CHOICES, value.householdSize) &&
    isChoice(PARENT_INCOME_CHOICES, value.parentIncome) &&
    isChoice(PARENT_ASSET_CHOICES, value.parentAsset) &&
    typeof value.parentsSkipped === 'boolean'
  );
}

/** 새로고침 때 입력 중이던 값을 복원한다. 손상된 값이나 동의 없는 값은 지운다. */
export function loadFlowSession(): FlowState | null {
  try {
    const saved: unknown = JSON.parse(window.sessionStorage.getItem(FLOW_SESSION_KEY) ?? 'null');
    if (isRecord(saved) && saved.consent === true && typeof saved.remember === 'boolean' && isAnswers(saved.answers)) {
      return { consent: true, remember: saved.remember, answers: saved.answers };
    }
    window.sessionStorage.removeItem(FLOW_SESSION_KEY);
  } catch {
    clearFlowSession();
  }
  return null;
}

export function clearFlowSession(): void {
  try {
    window.sessionStorage.removeItem(FLOW_SESSION_KEY);
  } catch {
    // 저장소를 사용할 수 없어도 메모리의 입력 흐름은 유지한다.
  }
}

export function saveFlowSession(state: FlowState): void {
  if (!state.consent) {
    clearFlowSession();
    return;
  }
  try {
    window.sessionStorage.setItem(FLOW_SESSION_KEY, JSON.stringify(state));
  } catch {
    // 용량 부족·차단 시에는 새로고침 복원 없이 계속한다.
  }
}
