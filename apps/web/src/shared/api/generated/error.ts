/* Generated from contracts/v1. Run pnpm types:generate; do not edit. */

/**
 * 코드 명명 lower_snake_case. HTTP: http_error=그 외 명시적인 HTTP 오류의 원래 상태, invalid_request=422, unsupported_domain=400, not_found=404, method_not_allowed=405, dependency_unavailable=503, internal_error=500, daily_limit_exceeded=429, calculation_limit_exceeded=503.
 */
export interface PublicErrorResponse {
  request_id: string;
  code:
    | 'http_error'
    | 'invalid_request'
    | 'unsupported_domain'
    | 'not_found'
    | 'method_not_allowed'
    | 'dependency_unavailable'
    | 'internal_error'
    | 'daily_limit_exceeded'
    | 'calculation_limit_exceeded';
  message: string;
}
