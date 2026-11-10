// 개발용 가상 상황 변화 결과. 상황 변화 API(POST /api/v1/simulations)가 생기기 전까지 개발 서버에서만 쓴다.
// 숫자는 기획서 8장 가상 정답(F 소득 절벽, G 취업)을 따랐다.
import type { ChangeSimulationResult, SimulationScenario } from '../../shared/api/simulations.ts';

const NOTE = '[가상] 개발용 예시 결과예요.';

const SAMPLES: Record<SimulationScenario, ChangeSimulationResult> = {
  employed: {
    scenario: 'employed',
    basis_period: '1y',
    before_total_won: 5_400_000,
    after_total_won: 2_400_000,
    annual: { before_won: 5_400_000, after_won: 2_400_000 },
    income_change: { from_won: 0, to_won: 24_000_000, assumption_note: '[가상] 취업 후 연 소득 2,400만 원으로 가정' },
    effective_marginal_rate: 12.5,
    lost: [{ policy_id: 'youth:example:P3', reason: '[가상] 취업하면 구직 중 조건을 벗어나요', amount_won: 3_000_000 }],
    gained: [],
    next_threshold: { text: '[가상] 연 소득 3,000만 원을 넘으면 청년 월세 지원도 끊겨요' },
    todo: null,
    calculation_note: NOTE,
  },
  income_up: {
    scenario: 'income_up',
    basis_period: '1y',
    before_total_won: 2_400_000,
    after_total_won: 1_500_000,
    annual: { before_won: 2_400_000, after_won: 1_500_000 },
    income_change: { from_won: 29_000_000, to_won: 31_000_000, assumption_note: '[가상] 연 소득 2,900만 → 3,100만 원으로 가정' },
    effective_marginal_rate: 45,
    lost: [{ policy_id: 'youth:example:P1', reason: '[가상] 연 소득 3,000만 원 기준 초과', amount_won: 2_400_000 }],
    gained: [{ policy_id: 'youth:example:P6', reason: '[가상] 청년 월세 지원이 빠져서 대신 조합에 들어가요', amount_won: 1_500_000 }],
    next_threshold: { text: '[가상] 연 소득 5,000만 원을 넘으면 전세대출 이자 지원도 끊겨요' },
    todo: null,
    calculation_note: NOTE,
  },
  independent: {
    scenario: 'independent',
    basis_period: '1y',
    before_total_won: 3_000_000,
    after_total_won: 5_400_000,
    annual: { before_won: 3_000_000, after_won: 5_400_000 },
    income_change: null,
    effective_marginal_rate: null,
    lost: [],
    gained: [{ policy_id: 'youth:example:P1', reason: '[가상] 혼자 월세로 살면 받을 수 있어요', amount_won: 2_400_000 }],
    next_threshold: null,
    todo: null,
    calculation_note: NOTE,
  },
  age_plus_one: {
    scenario: 'age_plus_one',
    basis_period: '1y',
    before_total_won: 5_400_000,
    after_total_won: 5_400_000,
    annual: { before_won: 5_400_000, after_won: 5_400_000 },
    income_change: null,
    effective_marginal_rate: null,
    lost: [],
    gained: [],
    next_threshold: { text: '[가상] 만 35세가 되면 청년 월세 지원과 구직활동 지원금이 끊겨요' },
    todo: null,
    calculation_note: NOTE,
  },
};

/** 개발 서버에서만 가상 결과를 돌려준다. 배포 화면에서는 늘 null */
export function devSampleSimulation(scenario: SimulationScenario): ChangeSimulationResult | null {
  return import.meta.env.DEV ? SAMPLES[scenario] : null;
}
