/* Generated from contracts/v1. Run pnpm types:generate; do not edit. */

export type AnalyticsEvent =
  | {
      name: 'consent_check';
    }
  | {
      name: 'remember_on';
    }
  | {
      name: 'step_view';
      step: number;
    }
  | {
      name: 'step_complete';
      step: number;
    }
  | {
      name: 'step_skip';
    }
  | {
      name: 'analyze_start';
    }
  | {
      name: 'analyze_success';
      duration_ms: number;
      portfolio_count: number;
    }
  | {
      name: 'analyze_fail';
      code:
        | 'http_error'
        | 'invalid_request'
        | 'unsupported_domain'
        | 'not_found'
        | 'method_not_allowed'
        | 'daily_limit_exceeded'
        | 'dependency_unavailable'
        | 'internal_error'
        | 'calculation_limit_exceeded'
        | 'timeout'
        | 'network_error'
        | 'invalid_response'
        | 'unknown';
    }
  | {
      name: 'period_change';
      period: '1y' | '2y' | 'all';
    }
  | {
      name: 'pv_toggle';
      on: boolean;
    }
  | {
      name: 'combo_select';
      rank: number;
    }
  | {
      name: 'detail_view';
      policy_id: string;
    }
  | {
      name: 'apply_click';
      policy_id: string;
      from: 'result' | 'detail';
      rank: number | null;
    }
  | {
      name: 'whatif_open';
    }
  | {
      name: 'whatif_select';
      scenario: 'employed' | 'income_up' | 'independent' | 'age_plus_one';
    }
  | {
      name: 'image_save';
    }
  | {
      name: 'clear_mine';
    };

/**
 * F11 허용 이벤트만 전달한다. 조건 값, URL, 오류 원문, 사용자 프로필은 받지 않는다. ID는 탭 메모리에서만 생성하는 임시 UUID다.
 */
export interface AnalyticsEventRequest {
  client_id: string;
  session_id: number;
  event: AnalyticsEvent;
}
