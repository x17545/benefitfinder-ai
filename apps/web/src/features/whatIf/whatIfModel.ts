// S4 상황이 바뀌면? 에서 쓰는 값. 계산은 서버가 하고, 화면은 읽기 좋은 글자만 만든다.
import type { ChangeSimulationResult, SimulationErrorReason, SimulationScenario } from '../../shared/api/simulations.ts';
import { PERIOD_TABS } from '../benefits/format.ts';

export const SCENARIO_CHIPS: readonly { value: SimulationScenario; label: string }[] = [
  { value: 'employed', label: '취업하면' },
  { value: 'income_up', label: '연 소득이 오르면' },
  { value: 'independent', label: '독립하면' },
  { value: 'age_plus_one', label: '나이가 1살 많아지면' },
];

/** 29,000,000 → "2,900만", 0 → "0" (만 원 단위, 반올림) */
export function manText(won: number): string {
  if (Math.round(won / 10_000) === 0) {
    return '0';
  }
  return `${Math.round(won / 10_000).toLocaleString('ko-KR')}만`;
}

/** 부호 붙인 만 원. +200만 / -240만 / 0 */
export function signedMan(won: number): string {
  if (won === 0) {
    return '0';
  }
  return `${won > 0 ? '+' : '-'}${manText(Math.abs(won))}`;
}

/** 문구 S4-SUM의 기간 이름. 1y → "1년" */
export function periodShort(period: ChangeSimulationResult['basis_period']): string {
  return PERIOD_TABS.find((tab) => tab.value === period)?.short ?? period;
}

/** 실제로 늘어나는 돈 (소득이 바뀌는 상황에서만). 줄어드는 지원금은 연간 환산 값 */
export interface NetChange {
  incomeFrom: number;
  incomeTo: number;
  incomeDiff: number;
  /** 줄어든 연간 지원금 (늘었으면 음수) */
  benefitLoss: number;
  /** 소득 증가 - 지원금 감소 */
  net: number;
}

export function netChange(result: ChangeSimulationResult): NetChange | null {
  const income = result.income_change;
  if ((result.scenario !== 'employed' && result.scenario !== 'income_up') ||
      income === null || income === undefined || income.to_won <= income.from_won) {
    return null;
  }
  const incomeDiff = income.to_won - income.from_won;
  const benefitLoss = result.annual.before_won - result.annual.after_won;
  return { incomeFrom: income.from_won, incomeTo: income.to_won, incomeDiff, benefitLoss, net: incomeDiff - benefitLoss };
}

/** 실효한계세율 막대 너비(%). 100%에서 멈춘다. 지원금이 줄지 않았으면 null */
export function rateBarWidth(rate: number | null | undefined): number | null {
  if (rate === null || rate === undefined || rate <= 0) {
    return null;
  }
  return Math.min(rate, 100);
}

/** 문구 S4-T: "더 번 돈 100원 중 45원이 지원금 감소로 사라져요 (실효한계세율 45%)" */
export function rateSentence(rate: number): string {
  const n = Math.round(rate * 10) / 10;
  const text = Number.isInteger(n) ? String(n) : n.toFixed(1);
  return `더 번 돈 100원 중 ${text}원이 지원금 감소로 사라져요 (실효한계세율 ${text}%)`;
}

/** 계산 실패 안내. 모두 '바뀌는 혜택이 없다'는 뜻이 아님을 먼저 밝힌다 (사용자 테스트 T2·T4·T18) */
export const SIMULATION_ERROR_COPY: Record<SimulationErrorReason, { title: string; description: string }> = {
  busy: {
    title: '지금은 계산하지 못했어요',
    description: '서비스 쪽에서 정책 자료를 잠시 불러오지 못했어요. 요청이 몰리면 생길 수 있어요. 잠시 뒤 다시 시도해 주세요.',
  },
  slow: {
    title: '계산이 오래 걸려 멈췄어요',
    description: '서비스 쪽 계산이 시간 안에 끝나지 않았어요. 잠시 뒤 다시 시도해 주세요.',
  },
  network: {
    title: '인터넷 연결이 원활하지 않아요',
    description: '연결 상태를 확인한 뒤 다시 시도해 주세요.',
  },
  input: {
    title: '이 조건으로는 계산할 수 없어요',
    description: '입력한 조건을 확인한 뒤 다시 분석해 주세요.',
  },
  server: {
    title: '계산 중 문제가 생겼어요',
    description: '서비스 쪽 문제로 결과를 받지 못했어요. 잠시 뒤 다시 시도해 주세요.',
  },
};

/** 끊기거나 새로 받는 정책이 없고 금액도 그대로인지 (계산은 정상으로 끝난 '변화 없음') */
export function isUnchanged(result: ChangeSimulationResult): boolean {
  return (result.lost ?? []).length === 0 && (result.gained ?? []).length === 0 &&
    result.after_total_won === result.before_total_won &&
    result.annual.after_won === result.annual.before_won;
}
