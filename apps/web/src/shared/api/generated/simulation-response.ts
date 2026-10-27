/* Generated from contracts/v1. Run pnpm types:generate; do not edit. */

/**
 * POST /api/v1/simulations 응답. before/after_total_won은 선택 기간(basis_period) 카드합, annual은 연간 환산 지원금으로 분리한다. effective_marginal_rate=(연간 지원금 감소분/연간 소득 증가분)*100. 세금/4대보험 제외, 지원금 변화만 계산. 소득이 바뀌지 않으면 income_change와 effective_marginal_rate는 null.
 */
export interface ChangeSimulationResult {
  scenario: 'employed' | 'income_up' | 'independent' | 'age_plus_one';
  /**
   * 선택 기간. before/after_total_won의 카드합 기준.
   */
  basis_period: '1y' | '2y' | 'all';
  /**
   * 선택 기간 카드합(변화 전).
   */
  before_total_won: number;
  /**
   * 선택 기간 카드합(변화 후).
   */
  after_total_won: number;
  /**
   * 연간 비율 계산용 지원금(annual). 실효비율 분자는 annual 감소분이다.
   */
  annual: {
    /**
     * 연간 환산 지원금(변화 전).
     */
    before_won: number;
    /**
     * 연간 환산 지원금(변화 후).
     */
    after_won: number;
  };
  /**
   * 연간 소득 변화(annual). from/to는 불명확 구간 대표값을 포함한 가상 시나리오 가정이며 사용자 확정 소득으로 표시·저장하지 않는다.
   */
  income_change?: {
    /**
     * 연간(annual) 소득.
     */
    from_won: number;
    /**
     * 연간(annual) 소득.
     */
    to_won: number;
    /**
     * 가상 시나리오 가정 표시 문구. 불명확 소득 구간의 대표값이며 사용자 확정 소득이 아님을 밝힌다.
     */
    assumption_note: string;
  } | null;
  /**
   * 연간 지원금 감소분/연간 소득 증가분*100.
   */
  effective_marginal_rate?: number | null;
  /**
   * policy_id로 추천 결과의 조합 정책 및 GET /api/v1/benefits/{benefit_id} 상세의 source·evidence에 연결한다. 변동 카드 자체에 source·evidence를 중복하지 않는다.
   */
  lost?: {
    policy_id: string;
    reason: string;
    amount_won?: number | null;
  }[];
  /**
   * policy_id로 추천 결과의 조합 정책 및 GET /api/v1/benefits/{benefit_id} 상세의 source·evidence에 연결한다. 변동 카드 자체에 source·evidence를 중복하지 않는다.
   */
  gained?: {
    policy_id: string;
    reason: string;
    amount_won?: number | null;
  }[];
  next_threshold?: {
    text: string;
  } | null;
  /**
   * AI 지금 할 일. 공고 원문 근거(evidence 1개 이상 필수)가 있을 때만 제공하고, 없으면 todo 전체를 null로 둔다.
   */
  todo?: {
    text: string;
    /**
     * @minItems 1
     */
    evidence: [Evidence, ...Evidence[]];
  } | null;
  /**
   * 예: 선택 기간 카드합과 연간 환산, 세금/4대보험 제외, 연간 지원금 변화/연간 소득 증가 기준.
   */
  calculation_note: string;
}
export interface Evidence {
  quote: string;
  location?: string;
  source: Source;
}
export interface Source {
  url: string;
  checked_at: string;
  document_version: string;
}
