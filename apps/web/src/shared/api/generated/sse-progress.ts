/* Generated from contracts/v1. Run pnpm types:generate; do not edit. */

/**
 * POST /api/v1/recommendations SSE의 progress 이벤트 data. 최종 result 이벤트의 data는 recommendation-response 계약을 따른다. error 이벤트의 data는 error 계약을 따른다.
 */
export interface SSEProgressEventData {
  /**
   * 애플리케이션이 실제로 처리한 단계(예: search_policies). RAG/Agent/LLM 사용은 필수가 아니며 구조화 정책 DB 검색과 같은 일반 처리 단계도 이 값을 쓴다.
   */
  step: string;
  state: 'running' | 'done' | 'failed';
  label: string;
  detail?: string | null;
}
