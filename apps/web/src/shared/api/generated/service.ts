/* Generated from contracts/v1. Run pnpm types:generate; do not edit. */

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
