// 조건 고르기 규칙 (기획서 4.3). 화면 없이 테스트할 수 있게 따로 둔다.
import { MILITARY_QUESTION_MIN_AGE } from '../../domains/youth/conditionOptions.ts';
import type { ContractType } from '../../domains/youth/conditionOptions.ts';
import type { ConditionAnswers } from './flowStore.ts';

/** 월세·전세면 보증금을 묻는다 */
export function needsDeposit(type: ContractType | null): boolean {
  return type === 'monthly_rent' || type === 'jeonse';
}

/** 월세면 월세 금액을 묻는다 */
export function needsRent(type: ContractType | null): boolean {
  return type === 'monthly_rent';
}

/** 35세 이상이면 군 복무를 묻는다 */
export function needsMilitary(age: number | null): boolean {
  return age !== null && age >= MILITARY_QUESTION_MIN_AGE;
}

/**
 * 여러 개 고르기. 이미 골랐으면 빼고, 아니면 더한다.
 * '해당 없음'(none)을 고르면 나머지는 풀리고, 다른 것을 고르면 '해당 없음'이 풀린다.
 */
export function toggleChoice<T extends string>(list: readonly T[], value: T, none?: T): T[] {
  if (list.includes(value)) {
    return list.filter((item) => item !== value);
  }
  if (none !== undefined && value === none) {
    return [value];
  }
  return [...list.filter((item) => item !== none), value];
}

/** 필수 값을 아직 안 고른 단계 번호 (12단계는 선택이라 없음) */
export function missingSteps(answers: ConditionAnswers): number[] {
  const missing: number[] = [];
  if (answers.age === null || (needsMilitary(answers.age) && answers.military === null)) {
    missing.push(1);
  }
  if (answers.regionCode === null) {
    missing.push(2);
  }
  if (answers.employmentStatus === null) {
    missing.push(3);
  }
  if (answers.school === null) {
    missing.push(4);
  }
  if (answers.marital === null) {
    missing.push(5);
  }
  if (answers.living === null || answers.homeOwnership === null || answers.householdHead === null) {
    missing.push(6);
  }
  if (
    answers.contractType === null ||
    (needsDeposit(answers.contractType) && answers.depositBand === null) ||
    (needsRent(answers.contractType) && answers.rentBand === null)
  ) {
    missing.push(7);
  }
  if (answers.incomeBand === null || answers.assetBand === null) {
    missing.push(8);
  }
  if (answers.special.length === 0) {
    missing.push(9);
  }
  if (answers.interests.length === 0) {
    missing.push(10);
  }
  if (answers.received.length === 0) {
    missing.push(11);
  }
  return missing;
}
