// S4 상황이 바뀌면? 에서 쓰는 값. 계산은 서버가 하고, 화면은 읽기 좋은 글자만 만든다.
import type { ChangeSimulationResult, SimulationScenario } from '../../shared/api/simulations.ts';
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
