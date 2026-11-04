import { useSyncExternalStore } from 'react';
import { clearAnalysis } from '../benefits/analysisStore.ts';
import { clearFlowSession, loadFlowSession, saveFlowSession } from './flowSession.ts';
import type {
  AssetBand,
  ContractType,
  DepositBand,
  EmploymentStatus,
  HomeOwnership,
  HouseholdHead,
  HouseholdSize,
  IncomeBand,
  Interest,
  Living,
  Marital,
  MilitaryChoice,
  ParentAssetBand,
  ParentIncomeBand,
  Received,
  RentBand,
  School,
  Special,
  Unknown,
} from '../../domains/youth/conditionOptions.ts';

/** S2 13단계에서 고른 값. null은 아직 안 고름 */
export interface ConditionAnswers {
  age: number | null;
  military: MilitaryChoice | null;
  sidoCode: string | null;
  regionCode: string | null;
  /** 확인 화면에 보여 줄 지역 이름. 예: 경기도 수원시 */
  regionLabel: string | null;
  employmentStatus: EmploymentStatus | null;
  school: School | null;
  marital: Marital | null;
  living: Living | null;
  homeOwnership: HomeOwnership | null;
  householdHead: HouseholdHead | null;
  contractType: ContractType | null;
  depositBand: DepositBand | null;
  rentBand: RentBand | null;
  incomeBand: IncomeBand | null;
  assetBand: AssetBand | null;
  special: Special[];
  interests: Interest[];
  received: Received[];
  householdSize: HouseholdSize | Unknown | null;
  parentIncome: ParentIncomeBand | Unknown | null;
  parentAsset: ParentAssetBand | Unknown | null;
  /** 12단계 [건너뛰기]를 눌렀는지 */
  parentsSkipped: boolean;
}

export const EMPTY_ANSWERS: ConditionAnswers = {
  age: null,
  military: null,
  sidoCode: null,
  regionCode: null,
  regionLabel: null,
  employmentStatus: null,
  school: null,
  marital: null,
  living: null,
  homeOwnership: null,
  householdHead: null,
  contractType: null,
  depositBand: null,
  rentBand: null,
  incomeBand: null,
  assetBand: null,
  special: [],
  interests: [],
  received: [],
  householdSize: null,
  parentIncome: null,
  parentAsset: null,
  parentsSkipped: false,
};

/**
 * 조건 고르기 흐름에서 화면끼리 함께 쓰는 값.
 * 메모리와 sessionStorage에 두어 새로고침에도 유지한다. 탭을 닫으면 사라진다.
 */
export interface FlowState {
  /** S1 필수 동의 */
  consent: boolean;
  /** S1 이 기기에 내 조건 기억하기 (선택, 30일) */
  remember: boolean;
  answers: ConditionAnswers;
}

const restored = loadFlowSession();
let state: FlowState = restored ?? { consent: false, remember: false, answers: EMPTY_ANSWERS };
// 입력 조건 없이 남은 과거 결과는 현재 조건의 결과로 재사용하지 않는다.
if (restored === null) {
  clearAnalysis();
}
const listeners = new Set<() => void>();

function emit(): void {
  listeners.forEach((listener) => {
    listener();
  });
}

export function getFlowState(): FlowState {
  return state;
}

export function updateFlowState(patch: Partial<Omit<FlowState, 'answers'>>): void {
  state = { ...state, ...patch };
  if (!state.consent) {
    clearAnalysis();
  }
  saveFlowSession(state);
  emit();
}

/** 고른 값 일부를 바꾼다. 나머지 값은 그대로 */
export function updateAnswers(patch: Partial<ConditionAnswers>): void {
  const answers = { ...state.answers, ...patch };
  if (JSON.stringify(answers) === JSON.stringify(state.answers)) {
    return;
  }
  clearAnalysis();
  state = { ...state, answers };
  saveFlowSession(state);
  emit();
}

/** [내 정보 지우기]는 새로고침용 입력과 동의도 함께 지운다. */
export function clearFlowState(): void {
  clearAnalysis();
  state = { consent: false, remember: false, answers: EMPTY_ANSWERS };
  clearFlowSession();
  emit();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** 값이 바뀌면 화면을 다시 그린다 */
export function useFlowState(): FlowState {
  return useSyncExternalStore(subscribe, getFlowState);
}
