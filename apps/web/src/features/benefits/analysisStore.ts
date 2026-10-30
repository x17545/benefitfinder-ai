import { useSyncExternalStore } from 'react';
import {
  isRecommendationResponse,
  postRecommendations,
  RecommendationError,
} from '../../shared/api/recommendations.ts';
import type {
  RecommendationRequest,
  RecommendationResponse,
  SSEProgressEventData,
} from '../../shared/api/recommendations.ts';

/** 15초 안 완료가 목표, 60초가 넘으면 실패 (기획서 4.4) */
const TIMEOUT_MS = 60_000;
/** 마지막 분석 결과. 탭을 닫으면 사라진다 (기획서 F09-6) */
const LAST_RESULT_KEY = 'ypm.lastResult';

export type AnalysisState =
  | { status: 'idle' }
  | { status: 'running'; progress: SSEProgressEventData[] }
  /** isSample: 개발용 예제 결과(contracts/fixtures)로 본 것 */
  | { status: 'done'; result: RecommendationResponse; isSample: boolean }
  /** error: 분석 실패(L-E1), limit: 하루 상한(L-E2) */
  | { status: 'failed'; reason: 'error' | 'limit' };

function readLastResult(): AnalysisState {
  try {
    const saved: unknown = JSON.parse(window.sessionStorage.getItem(LAST_RESULT_KEY) ?? 'null');
    if (
      typeof saved === 'object' &&
      saved !== null &&
      'result' in saved &&
      isRecommendationResponse(saved.result)
    ) {
      const isSample = 'isSample' in saved && saved.isSample === true;
      return { status: 'done', result: saved.result, isSample };
    }
  } catch {
    // 저장소를 못 쓰는 환경이면 새로 분석한다
  }
  return { status: 'idle' };
}

function saveLastResult(result: RecommendationResponse, isSample: boolean): void {
  try {
    window.sessionStorage.setItem(LAST_RESULT_KEY, JSON.stringify({ result, isSample }));
  } catch {
    // 저장 실패해도 화면은 계속 보여 준다
  }
}

let state: AnalysisState = typeof window === 'undefined' ? { status: 'idle' } : readLastResult();
let controller: AbortController | null = null;
const listeners = new Set<() => void>();

function setState(next: AnalysisState): void {
  state = next;
  listeners.forEach((listener) => {
    listener();
  });
}

function mergeProgress(list: SSEProgressEventData[], item: SSEProgressEventData): SSEProgressEventData[] {
  const index = list.findIndex((entry) => entry.step === item.step);
  if (index === -1) {
    return [...list, item];
  }
  return list.map((entry, i) => (i === index ? item : entry));
}

/** 분석을 시작한다. 이미 하고 있던 분석은 취소한다 */
export function startAnalysis(request: RecommendationRequest): void {
  clearAnalysis();
  const current = new AbortController();
  controller = current;
  let timedOut = false;
  const timer = window.setTimeout(() => {
    timedOut = true;
    current.abort();
  }, TIMEOUT_MS);

  setState({ status: 'running', progress: [] });

  postRecommendations(request, {
    signal: current.signal,
    onProgress: (progress) => {
      if (controller === current && state.status === 'running') {
        setState({ status: 'running', progress: mergeProgress(state.progress, progress) });
      }
    },
  })
    .then((result) => {
      if (controller !== current) {
        return;
      }
      saveLastResult(result, false);
      setState({ status: 'done', result, isSample: false });
    })
    .catch((error: unknown) => {
      if (controller !== current || (current.signal.aborted && !timedOut)) {
        return;
      }
      const limit = error instanceof RecommendationError && error.code === 'daily_limit_exceeded';
      setState({ status: 'failed', reason: limit ? 'limit' : 'error' });
    })
    .finally(() => {
      window.clearTimeout(timer);
      if (controller === current) {
        controller = null;
      }
    });
}

/** 진행 중인 분석을 취소한다 (로딩 중 뒤로 가기) */
export function cancelAnalysis(): void {
  if (controller === null) {
    return;
  }
  const current = controller;
  controller = null;
  current.abort();
  if (state.status === 'running') {
    setState({ status: 'idle' });
  }
}

/** 저장된 마지막 결과까지 지운다 ([내 정보 지우기]) */
export function clearAnalysis(): void {
  cancelAnalysis();
  try {
    window.sessionStorage.removeItem(LAST_RESULT_KEY);
  } catch {
    // 지울 것이 없으면 그대로 둔다
  }
  setState({ status: 'idle' });
}

/**
 * 개발 서버(pnpm dev)에서만: 백엔드 없이 화면을 볼 수 있게 contracts 예제 결과를 넣는다.
 * 실제 서비스(빌드)에서는 이 코드가 빠진다.
 */
export async function showSampleResult(): Promise<void> {
  if (!import.meta.env.DEV) {
    return;
  }
  const { default: raw } = await import('../../../../../contracts/fixtures/recommendation-response.json?raw');
  const result: unknown = JSON.parse(raw);
  if (isRecommendationResponse(result)) {
    saveLastResult(result, true);
    setState({ status: 'done', result, isSample: true });
  }
}

/** 지금 분석 상태 (화면 밖에서 읽을 때) */
export function getAnalysisState(): AnalysisState {
  return state;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useAnalysis(): AnalysisState {
  return useSyncExternalStore(subscribe, getAnalysisState);
}
