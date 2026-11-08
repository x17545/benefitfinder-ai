import { useCallback, useEffect, useState } from 'react';
import { postSimulation } from '../../shared/api/simulations.ts';
import type {
  ChangeSimulationRequest,
  ChangeSimulationResult,
  SimulationOutcome,
  SimulationScenario,
} from '../../shared/api/simulations.ts';
import { devSampleSimulation } from './devSampleSimulations.ts';

export type SimulationState =
  | { status: 'loading' }
  /** isSample: 개발용 가상 결과로 본 것 */
  | { status: 'ok'; result: ChangeSimulationResult; isSample: boolean }
  | { status: 'limit' }
  | { status: 'error' };

/** 서버 결과를 화면 상태로. 개발 서버에서 계산 기능이 없으면 가상 결과를 쓴다 */
export function toSimulationState(scenario: SimulationScenario, outcome: SimulationOutcome): SimulationState {
  if (outcome.status === 'ok') {
    return { status: 'ok', result: outcome.result, isSample: false };
  }
  if (outcome.status === 'limit') {
    return outcome;
  }
  const sample = devSampleSimulation(scenario);
  return sample !== null ? { status: 'ok', result: sample, isSample: true } : { status: 'error' };
}

/** 상황 칩을 바꿀 때마다 서버에 다시 계산을 요청한다 (기획서 4.6-1) */
export function useSimulation(request: ChangeSimulationRequest | null): { state: SimulationState; retry: () => void } {
  const [attempt, setAttempt] = useState(0);
  const [loaded, setLoaded] = useState<{ key: string; state: SimulationState } | null>(null);
  const body = request === null ? '' : JSON.stringify(request);
  const key = `${body}#${String(attempt)}`;

  useEffect(() => {
    if (body === '') {
      return undefined;
    }
    const parsed = JSON.parse(body) as ChangeSimulationRequest;
    const controller = new AbortController();
    void postSimulation(parsed, fetch, controller.signal).then((outcome) => {
      if (!controller.signal.aborted) {
        setLoaded({ key, state: toSimulationState(parsed.scenario, outcome) });
      }
    });
    return () => {
      controller.abort();
    };
  }, [body, key]);

  const retry = useCallback(() => {
    setAttempt((count) => count + 1);
  }, []);

  return { state: loaded?.key === key ? loaded.state : { status: 'loading' }, retry };
}
