import type { ChangeSimulationRequest } from './generated/simulation-request.ts';
import type { ChangeSimulationResult } from './generated/simulation-response.ts';
import { API_PREFIX } from '../config/api.ts';
import {
  arrayOf, isEvidence, isMoney, isNonEmptyString, isNullableMoney, isRecord, optional,
} from './responseValidation.ts';

export type { ChangeSimulationRequest, ChangeSimulationResult };
export type SimulationScenario = ChangeSimulationResult['scenario'];

/** POST 상황 변화 계산. contracts/v1 simulation-request / response */
export const SIMULATIONS_PATH = `${API_PREFIX}/simulations`;

const SCENARIOS: readonly string[] = ['employed', 'income_up', 'independent', 'age_plus_one'];
const PERIODS: readonly string[] = ['1y', '2y', 'all'];

function isChange(value: unknown): boolean {
  return isRecord(value) && isNonEmptyString(value.policy_id) && isNonEmptyString(value.reason) &&
    optional(value.amount_won, isNullableMoney);
}

function isIncomeChange(value: unknown): boolean {
  return value === null || (isRecord(value) && isMoney(value.from_won) && isMoney(value.to_won) &&
    isNonEmptyString(value.assumption_note));
}

/** 상황 변화 결과가 약속(contracts/v1)의 모양과 맞는지 확인한다 */
export function isSimulationResult(value: unknown): value is ChangeSimulationResult {
  return (
    isRecord(value) &&
    typeof value.scenario === 'string' && SCENARIOS.includes(value.scenario) &&
    typeof value.basis_period === 'string' && PERIODS.includes(value.basis_period) &&
    optional(value.virtual_data, (item) => typeof item === 'boolean') &&
    isMoney(value.before_total_won) && isMoney(value.after_total_won) &&
    isRecord(value.annual) && isMoney(value.annual.before_won) && isMoney(value.annual.after_won) &&
    isNonEmptyString(value.calculation_note) &&
    optional(value.income_change, isIncomeChange) &&
    optional(value.effective_marginal_rate, (item) => item === null || (typeof item === 'number' && Number.isFinite(item))) &&
    optional(value.lost, (item) => arrayOf(item, isChange)) &&
    optional(value.gained, (item) => arrayOf(item, isChange)) &&
    optional(value.next_threshold, (item) => item === null || (isRecord(item) && isNonEmptyString(item.text))) &&
    optional(value.todo, (item) => item === null || (isRecord(item) && isNonEmptyString(item.text) && arrayOf(item.evidence, isEvidence, 1)))
  );
}

export type SimulationOutcome =
  | { status: 'ok'; result: ChangeSimulationResult }
  /** 하루 사용 상한 (HTTP 429) */
  | { status: 'limit' }
  /** 서버에 계산 기능이 없음 (HTTP 404) */
  | { status: 'notAvailable' }
  | { status: 'error' };

/** POST /api/v1/simulations */
export async function postSimulation(
  request: ChangeSimulationRequest,
  fetchFn: typeof fetch = fetch,
  signal?: AbortSignal,
): Promise<SimulationOutcome> {
  try {
    const response = await fetchFn(SIMULATIONS_PATH, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(request),
      signal,
    });
    if (response.status === 429) {
      return { status: 'limit' };
    }
    if (response.status === 404) {
      return { status: 'notAvailable' };
    }
    if (!response.ok) {
      return { status: 'error' };
    }
    const body: unknown = await response.json();
    return isSimulationResult(body) && body.scenario === request.scenario &&
      body.basis_period === (request.options?.period ?? '1y')
      ? { status: 'ok', result: body } : { status: 'error' };
  } catch {
    return { status: 'error' };
  }
}
