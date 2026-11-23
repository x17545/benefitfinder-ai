import type { ChangeSimulationRequest } from './generated/simulation-request.ts';
import type { ChangeSimulationResult } from './generated/simulation-response.ts';
import { API_PREFIX } from '../config/api.ts';
import {
  arrayOf, isAmountBreakdown, isEvidence, isMoney, isNonEmptyString, isNullableMoney, isRecord, optional,
} from './responseValidation.ts';

export type { ChangeSimulationRequest, ChangeSimulationResult };
export type SimulationScenario = ChangeSimulationResult['scenario'];

/** POST 상황 변화 계산. contracts/v1 simulation-request / response */
export const SIMULATIONS_PATH = `${API_PREFIX}/simulations`;

const SCENARIOS: readonly string[] = ['employed', 'income_up', 'independent', 'age_plus_one'];
const PERIODS: readonly string[] = ['1y', '2y', 'all'];

function isChange(value: unknown): boolean {
  if (!isRecord(value) || !isNonEmptyString(value.policy_id) || !isNonEmptyString(value.reason) ||
    !optional(value.amount_won, isNullableMoney) || !optional(value.amount_breakdown, isAmountBreakdown)) {
    return false;
  }
  return value.amount_breakdown === undefined || isMoney(value.amount_won);
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
    optional(value.annual.before_amount_breakdown, isAmountBreakdown) &&
    optional(value.annual.after_amount_breakdown, isAmountBreakdown) &&
    isNonEmptyString(value.calculation_note) &&
    optional(value.before_amount_breakdown, isAmountBreakdown) &&
    optional(value.after_amount_breakdown, isAmountBreakdown) &&
    optional(value.income_change, isIncomeChange) &&
    optional(value.effective_marginal_rate, (item) => item === null || (typeof item === 'number' && Number.isFinite(item))) &&
    optional(value.lost, (item) => arrayOf(item, isChange)) &&
    optional(value.gained, (item) => arrayOf(item, isChange)) &&
    optional(value.next_threshold, (item) => item === null || (isRecord(item) && isNonEmptyString(item.text))) &&
    optional(value.todo, (item) => item === null || (isRecord(item) && isNonEmptyString(item.text) && arrayOf(item.evidence, isEvidence, 1)))
  );
}

/**
 * 계산 실패 이유. 사용자 테스트에서 오류와 '바뀌는 혜택 없음'을 구분하지 못했다는 의견이 있어 나눠 안내한다.
 * busy: 서버가 정책 자료를 잠시 쓸 수 없음(503 dependency_unavailable 등),
 * slow: 계산 시간 한도를 넘음(503 calculation_limit_exceeded, 화면의 60초 대기 초과),
 * network: 인터넷 연결 실패, input: 입력 조건을 계산할 수 없음(400·422), server: 그 밖의 오류·잘못된 응답
 */
export type SimulationErrorReason = 'busy' | 'slow' | 'network' | 'input' | 'server';

export type SimulationOutcome =
  | { status: 'ok'; result: ChangeSimulationResult }
  /** 하루 사용 상한 (HTTP 429) */
  | { status: 'limit' }
  /** 서버에 계산 기능이 없음 (HTTP 404) */
  | { status: 'notAvailable' }
  | { status: 'error'; reason?: SimulationErrorReason };

/** 실패 응답의 오류 코드(contracts/v1 ErrorResponse.code)로 이유를 고른다 */
async function errorReason(response: Response): Promise<SimulationErrorReason> {
  let code: unknown;
  try {
    const body: unknown = await response.json();
    code = isRecord(body) ? body.code : null;
  } catch {
    code = null;
  }
  if (code === 'calculation_limit_exceeded') {
    return 'slow';
  }
  if (response.status === 503 || code === 'dependency_unavailable') {
    return 'busy';
  }
  if (response.status === 400 || response.status === 422) {
    return 'input';
  }
  return 'server';
}

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
      return { status: 'error', reason: await errorReason(response) };
    }
    const body: unknown = await response.json();
    return isSimulationResult(body) && body.scenario === request.scenario &&
      body.basis_period === (request.options?.period ?? '1y')
      ? { status: 'ok', result: body } : { status: 'error', reason: 'server' };
  } catch (error) {
    // fetch 자체가 실패하면 연결 문제(TypeError), 응답 본문을 읽다 실패하면 서버 응답 문제
    return { status: 'error', reason: error instanceof TypeError ? 'network' : 'server' };
  }
}
