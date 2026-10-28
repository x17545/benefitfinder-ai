import { useSyncExternalStore } from 'react';

/**
 * 조건 고르기 흐름에서 화면끼리 함께 쓰는 값.
 * 서버로 보내지 않고 이 브라우저 탭의 메모리에만 둔다.
 * 5단계에서 13단계 답(조건)을 여기에 더한다.
 */
export interface FlowState {
  /** S1 필수 동의 */
  consent: boolean;
  /** S1 이 기기에 내 조건 기억하기 (선택, 30일) */
  remember: boolean;
}

let state: FlowState = { consent: false, remember: false };
const listeners = new Set<() => void>();

export function getFlowState(): FlowState {
  return state;
}

export function updateFlowState(patch: Partial<FlowState>): void {
  state = { ...state, ...patch };
  listeners.forEach((listener) => {
    listener();
  });
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
