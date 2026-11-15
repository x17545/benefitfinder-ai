/* Generated from contracts/v1. Run pnpm types:generate; do not edit. */

/**
 * GET /api/v1/meta 응답. base_rate가 null이면 현재가치 0 할인율로 확정하지 않고 discount 상태를 unknown으로 둔다.
 */
export interface ServiceMeta {
  data_updated_at: string;
  base_rate: number | null;
  base_rate_status: 'verified' | 'unknown';
  daily_remaining: number;
  /**
   * true는 DB 미설정의 개발용 가상 정책·금리 메타데이터다. 실제 데이터의 갱신·금리 검증으로 표시하지 않는다.
   */
  virtual_data?: boolean;
}
