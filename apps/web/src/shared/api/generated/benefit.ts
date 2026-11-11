/* Generated from contracts/v1. Run pnpm types:generate; do not edit. */

/**
 * 정규화 혜택. 기존 필수(id/domain/title/summary/provider/categories/application/eligibility_conditions/source/domain_data) 유지. benefit_amount/reviewed/conflicts/lifetime_once는 조합 계산용 선택 확장. application.status에 always 값을 두지 않고 상시는 open+공식 상시 근거로 표현한다. 날짜 null을 open으로 추정하지 않는다.
 */
export interface NormalizedBenefit {
  id: string;
  domain: string;
  title: string;
  summary: string;
  provider: string;
  categories: string[];
  /**
   * 백엔드 가상 정책 상세. 실제 모집·신청 정보로 표시하지 않는다.
   */
  virtual_data?: boolean;
  application: {
    status: 'open' | 'closed' | 'upcoming' | 'unknown';
    starts_on: string | null;
    ends_on: string | null;
    url: string | null;
  };
  eligibility_conditions: {
    id: string;
    description: string;
    /**
     * @minItems 1
     */
    evidence: [Evidence, ...Evidence[]];
  }[];
  service?: InKindService;
  benefit_amount?: {
    type?: 'monthly' | 'lump_sum' | 'maturity' | 'interest' | null;
    /**
     * 원 단위 정수. 미상은 null.
     */
    amount_won?: number | null;
    period_months?: number | null;
    max_total_won?: number | null;
    /**
     * 현재가치 계산용 지급 시점. 불명확하면 null로 두고 needs_review로 표시한다.
     */
    pay_timing?: 'monthly_from_start' | 'at_maturity' | 'yearly' | 'once' | null;
    amount_text?: string | null;
    amount_status?: 'verified' | 'unknown';
  } | null;
  /**
   * 금액/중복/지급시점 검수 완료. true만 조합 계산에 사용.
   */
  reviewed?: boolean | null;
  conflicts?:
    | {
        policy_id: string;
        /**
         * @minItems 1
         */
        evidence: [Evidence, ...Evidence[]];
      }[]
    | null;
  lifetime_once?: boolean | null;
  source: Source;
  domain_data: {
    [k: string]: unknown;
  };
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
/**
 * F14 공개 서비스 내용과 서버 환산 결과. estimated_value_won은 근거 확인 단가 × 횟수이며 현금 조합에는 포함하지 않는다. 검증하지 못한 단가와 환산 근거는 모두 null.
 */
export interface InKindService {
  item: string;
  unit: string;
  quantity: number;
  unit_value_won: number | null;
  value_basis: string | null;
  value_source_url: string | null;
  value_checked_at: string | null;
  value_status: 'verified' | 'unknown';
  estimated_value_won: number | null;
}
