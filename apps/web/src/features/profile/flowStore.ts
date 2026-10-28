import { useSyncExternalStore } from 'react';
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
 * 서버로 보내지 않고 이 브라우저 탭의 메모리에만 둔다.
 */
export interface FlowState {
  /** S1 필수 동의 */
  consent: boolean;
  /** S1 이 기기에 내 조건 기억하기 (선택, 30일) */
  remember: boolean;
  answers: ConditionAnswers;
}

let state: FlowState = { consent: false, remember: false, answers: EMPTY_ANSWERS };
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
  emit();
}

/** 고른 값 일부를 바꾼다. 나머지 값은 그대로 */
export function updateAnswers(patch: Partial<ConditionAnswers>): void {
  state = { ...state, answers: { ...state.answers, ...patch } };
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
