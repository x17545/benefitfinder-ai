/* Generated from contracts/v1. Run pnpm types:generate; do not edit. */

export type Categories = string[];
/**
 * 선택 탭의 계산 결과. 6.3 예시 위치에 fallback_list를 둔다.
 */
export type ResultView = {
  /**
   * @maxItems 3
   */
  portfolios: [] | [Portfolio] | [Portfolio, Portfolio] | [Portfolio, Portfolio, Portfolio];
  why?: {
    gap_won?: number | null;
    second_total_won?: number | null;
    deciding_conflict?: string[];
  } | null;
  excluded?: ExcludedItem[];
  not_eligible?: {
    policy_id: string;
    /**
     * @minItems 1
     */
    failed: [string, ...string[]];
  }[];
  not_yet_reviewed?: {
    policy_id: string;
    eligibility_summary: 'met' | 'not_met' | 'needs_review';
    /**
     * F12 승인 preview용 실제 정책명. review_explanation이 있을 때 필수다.
     */
    policy_name?: string;
    /**
     * F12 승인 preview가 확인한 범위 메모(조합/자격/금액 확정 전). review_explanation이 있을 때 필수다.
     */
    review_scope?: string;
    /**
     * F12 승인 preview 표시. true 고정이며 수급·신청 보장이 아니다. review_explanation이 있을 때 필수다.
     */
    verification_only?: true;
    review_explanation?: PortfolioPolicy1;
  }[];
  closed_now?: {
    policy_id: string;
    last_period?: string | null;
    next_expected?: string | null;
  }[];
  /**
   * 선택 탭의 목록(6.3 예시 위치). 후보 1개 이하라 묶음을 못 만들 때 최대 5개. 탭과 무관하게 후보 수로 정해지므로 통상 6가지 탭에서 동일하다.
   *
   * @maxItems 5
   */
  fallback_list?:
    | []
    | [FallbackItem]
    | [FallbackItem, FallbackItem]
    | [FallbackItem, FallbackItem, FallbackItem]
    | [FallbackItem, FallbackItem, FallbackItem, FallbackItem]
    | [FallbackItem, FallbackItem, FallbackItem, FallbackItem, FallbackItem]
    | null;
  /**
   * PV 금리 unknown 등 계산 불가 분기는 unavailable. unavailable이면 portfolios 빈 배열, 합계 추정 안 함(정상 raw 분기만 활성).
   */
  calculation_status: 'available' | 'unavailable';
  /**
   * available이면 null. unavailable이면 사유 필수.
   */
  unavailable_reason: 'missing_discount_rate' | 'unknown_payment_timing' | null;
};
export type PortfolioPolicy = {
  policy_id: string;
  amount_in_period_won?: number | null;
  original_amount_won?: number | null;
  amount_status?: 'verified' | 'unknown';
  eligibility: ConditionResult[];
  /**
   * 6장 초안 필드명을 호환을 위해 유지한 추천 이유 텍스트. 템플릿을 포함하며 생성형 AI 작성 여부는 ai_generated 플래그와 함께 해석한다. 최종 필드명은 회의에서 정리한다. 민감 특수조건/부모정보를 쓰지 않는다.
   */
  ai_reason?: string | null;
  /**
   * 이 정책 설명을 생성형 AI가 작성했으면 true, 기본 근거 템플릿이면 false다. 생략하면 이전 응답과 같이 취급하며 최상위 ai_generated로만 해석한다.
   */
  ai_generated?: boolean;
  /**
   * AI 생성 설명에 사용한 게시 조항 원문 인용 최대 5개. quote=게시 조항 전체 원문, location=조항 ID(필수), source=url/checked_at/document_version. evidence 정의를 재사용하되 location을 필수로 한다(다른 evidence의 location 선택성은 유지).
   *
   * @maxItems 5
   */
  ai_sources?:
    | []
    | [
        Evidence & {
          location: string;
          [k: string]: unknown;
        }
      ]
    | [
        Evidence & {
          location: string;
          [k: string]: unknown;
        },
        Evidence & {
          location: string;
          [k: string]: unknown;
        }
      ]
    | [
        Evidence & {
          location: string;
          [k: string]: unknown;
        },
        Evidence & {
          location: string;
          [k: string]: unknown;
        },
        Evidence & {
          location: string;
          [k: string]: unknown;
        }
      ]
    | [
        Evidence & {
          location: string;
          [k: string]: unknown;
        },
        Evidence & {
          location: string;
          [k: string]: unknown;
        },
        Evidence & {
          location: string;
          [k: string]: unknown;
        },
        Evidence & {
          location: string;
          [k: string]: unknown;
        }
      ]
    | [
        Evidence & {
          location: string;
          [k: string]: unknown;
        },
        Evidence & {
          location: string;
          [k: string]: unknown;
        },
        Evidence & {
          location: string;
          [k: string]: unknown;
        },
        Evidence & {
          location: string;
          [k: string]: unknown;
        },
        Evidence & {
          location: string;
          [k: string]: unknown;
        }
      ];
  source: Source;
  amount_breakdown?: AmountBreakdown;
} & {
  policy_id: string;
  amount_in_period_won?: number | null;
  original_amount_won?: number | null;
  amount_status?: 'verified' | 'unknown';
  eligibility: ConditionResult[];
  /**
   * 6장 초안 필드명을 호환을 위해 유지한 추천 이유 텍스트. 템플릿을 포함하며 생성형 AI 작성 여부는 ai_generated 플래그와 함께 해석한다. 최종 필드명은 회의에서 정리한다. 민감 특수조건/부모정보를 쓰지 않는다.
   */
  ai_reason?: string | null;
  /**
   * 이 정책 설명을 생성형 AI가 작성했으면 true, 기본 근거 템플릿이면 false다. 생략하면 이전 응답과 같이 취급하며 최상위 ai_generated로만 해석한다.
   */
  ai_generated?: boolean;
  /**
   * AI 생성 설명에 사용한 게시 조항 원문 인용 최대 5개. quote=게시 조항 전체 원문, location=조항 ID(필수), source=url/checked_at/document_version. evidence 정의를 재사용하되 location을 필수로 한다(다른 evidence의 location 선택성은 유지).
   *
   * @maxItems 5
   */
  ai_sources?:
    | []
    | [
        Evidence & {
          location: string;
          [k: string]: unknown;
        }
      ]
    | [
        Evidence & {
          location: string;
          [k: string]: unknown;
        },
        Evidence & {
          location: string;
          [k: string]: unknown;
        }
      ]
    | [
        Evidence & {
          location: string;
          [k: string]: unknown;
        },
        Evidence & {
          location: string;
          [k: string]: unknown;
        },
        Evidence & {
          location: string;
          [k: string]: unknown;
        }
      ]
    | [
        Evidence & {
          location: string;
          [k: string]: unknown;
        },
        Evidence & {
          location: string;
          [k: string]: unknown;
        },
        Evidence & {
          location: string;
          [k: string]: unknown;
        },
        Evidence & {
          location: string;
          [k: string]: unknown;
        }
      ]
    | [
        Evidence & {
          location: string;
          [k: string]: unknown;
        },
        Evidence & {
          location: string;
          [k: string]: unknown;
        },
        Evidence & {
          location: string;
          [k: string]: unknown;
        },
        Evidence & {
          location: string;
          [k: string]: unknown;
        },
        Evidence & {
          location: string;
          [k: string]: unknown;
        }
      ];
  source: Source;
  amount_breakdown?: AmountBreakdown;
};
/**
 * F12 승인 preview 설명. portfolio_policy를 재사용하며 금액 미확정(null)·자격 미확정(needs_review 또는 빈 배열)으로만 둔다. 있을 때 policy_name/review_scope/verification_only가 필수이며 policy_id 동일성은 애플리케이션 검증으로 확인한다.
 */
export type PortfolioPolicy1 = {
  policy_id: string;
  amount_in_period_won?: number | null;
  original_amount_won?: number | null;
  amount_status?: 'verified' | 'unknown';
  eligibility: ConditionResult[];
  /**
   * 6장 초안 필드명을 호환을 위해 유지한 추천 이유 텍스트. 템플릿을 포함하며 생성형 AI 작성 여부는 ai_generated 플래그와 함께 해석한다. 최종 필드명은 회의에서 정리한다. 민감 특수조건/부모정보를 쓰지 않는다.
   */
  ai_reason?: string | null;
  /**
   * 이 정책 설명을 생성형 AI가 작성했으면 true, 기본 근거 템플릿이면 false다. 생략하면 이전 응답과 같이 취급하며 최상위 ai_generated로만 해석한다.
   */
  ai_generated?: boolean;
  /**
   * AI 생성 설명에 사용한 게시 조항 원문 인용 최대 5개. quote=게시 조항 전체 원문, location=조항 ID(필수), source=url/checked_at/document_version. evidence 정의를 재사용하되 location을 필수로 한다(다른 evidence의 location 선택성은 유지).
   *
   * @maxItems 5
   */
  ai_sources?:
    | []
    | [
        Evidence & {
          location: string;
          [k: string]: unknown;
        }
      ]
    | [
        Evidence & {
          location: string;
          [k: string]: unknown;
        },
        Evidence & {
          location: string;
          [k: string]: unknown;
        }
      ]
    | [
        Evidence & {
          location: string;
          [k: string]: unknown;
        },
        Evidence & {
          location: string;
          [k: string]: unknown;
        },
        Evidence & {
          location: string;
          [k: string]: unknown;
        }
      ]
    | [
        Evidence & {
          location: string;
          [k: string]: unknown;
        },
        Evidence & {
          location: string;
          [k: string]: unknown;
        },
        Evidence & {
          location: string;
          [k: string]: unknown;
        },
        Evidence & {
          location: string;
          [k: string]: unknown;
        }
      ]
    | [
        Evidence & {
          location: string;
          [k: string]: unknown;
        },
        Evidence & {
          location: string;
          [k: string]: unknown;
        },
        Evidence & {
          location: string;
          [k: string]: unknown;
        },
        Evidence & {
          location: string;
          [k: string]: unknown;
        },
        Evidence & {
          location: string;
          [k: string]: unknown;
        }
      ];
  source: Source;
  amount_breakdown?: AmountBreakdown;
};

/**
 * POST /api/v1/recommendations 최종 result. 13단계 선택지 단일 계약이며 결과 추가 질문 필드는 없다. 3기간(1y/2y/all)xPV on/off 6가지를 results에 한 번에 담는다. view는 전역 초기 기준(기본 선택 1y+PV 끔, 즉 results[1y][pv_off]의 결과와 일치)이다. 기간/PV 탭 전환 시 화면은 다시 묻지 않고 선택 탭의 result_view를 따라 표시 섹션을 바꾼다(portfolios가 있으면 카드, 없으면 그 탭의 fallback_list, 둘 다 없으면 비어 있음). 조건 판정의 met/not_met은 근거 1개 이상 필수, 미상은 needs_review. need_check 상태값은 거부된다. 모든 업무 불변식을 스키마 하나로 강제하지 않고 구조+문서/테스트로 검증한다.
 */
export interface RecommendationResponse {
  request_id: string;
  domain: string;
  /**
   * true면 가상 정책·금액의 개발용 계산이다. 화면과 저장 이미지에 실제 추천이 아님을 표시한다. 생략은 실제 자료 검증 완료를 뜻하지 않는다.
   */
  virtual_data?: boolean;
  /**
   * F14 비현금 서비스. 생략은 서비스 미지원, 빈 배열은 조회했지만 후보 없음. 기간·현재가치와 별도.
   */
  services?: RecommendedService[];
  /**
   * 근거 있는 서비스 환산액만 합한 서버 값. 환산 가능 항목이 없거나 안전 정수 범위를 넘으면 null.
   */
  services_total_won?: number | null;
  /**
   * 전역 초기 기준. results[1y][pv_off]의 표시 섹션과 일치한다.
   */
  view: 'portfolio' | 'fallback_list' | 'empty';
  results: {
    '1y': PeriodResult;
    '2y': PeriodResult;
    all: PeriodResult;
  };
  /**
   * 응답 중 AI 생성 설명이 하나라도 있으면 true, 모두 기본 근거 템플릿이면 false다. 각 정책의 ai_generated가 해당 항목 기준이다.
   */
  ai_generated: boolean;
  data_updated_at: string;
  /**
   * 현재가치 할인율(연). 기준금리 미확인이면 null. null일 때 0 할인율로 확정하지 않는다.
   */
  discount_rate: number | null;
  discount_rate_status: 'verified' | 'unknown';
}
export interface RecommendedService {
  policy_id: string;
  title: string;
  categories: Categories;
  application: Application;
  source: Source;
  service: InKindService;
  eligibility: ConditionResult[];
}
export interface Application {
  status: 'open' | 'closed' | 'upcoming' | 'unknown';
  starts_on: string | null;
  ends_on: string | null;
  url: string | null;
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
export interface ConditionResult {
  condition_id: string;
  status: 'met' | 'not_met' | 'needs_review';
  reason: string;
  evidence: Evidence[];
}
export interface Evidence {
  quote: string;
  location?: string;
  source: Source;
}
export interface PeriodResult {
  pv_off: ResultView;
  pv_on: ResultView;
}
export interface Portfolio {
  rank: number;
  /**
   * 원 단위 정수. 조합은 검수 확정 금액만 포함하므로 항상 정수. 미상 시나리오는 unavailable 분기로 표현하고 여기서 null을 쓰지 않는다.
   */
  total_won: number;
  present_value_applied?: boolean;
  /**
   * @minItems 1
   */
  policies: [PortfolioPolicy, ...PortfolioPolicy[]];
  given_up?: {
    policy_id: string;
    amount_won?: number | null;
    amount_breakdown?: AmountBreakdown;
  }[];
  unverified_pairs?: {
    a: string;
    b: string;
    note: string;
  }[];
  amount_breakdown?: AmountBreakdown;
}
/**
 * 만기 지급이 포함된 경우의 명목 현금·미지급 예상 적립액 구분. PV off 합계는 두 값의 합, PV on 합계는 기간 내 현금의 현재가치다.
 */
export interface AmountBreakdown {
  /**
   * 선택 기간 내 실제 현금 지급 예정액. 현재가치 할인 전 명목금액이다.
   */
  cash_in_period_won: number;
  /**
   * 선택 기간까지 적립되지만 만기 전이라 아직 지급되지 않는 검수된 예상 지원금. 본인 납입금 제외. 현재가치 합계에는 넣지 않는다.
   */
  expected_accrual_won: number;
}
export interface ExcludedItem {
  policy_id: string;
  reason: string;
  /**
   * @minItems 1
   */
  evidence: [Evidence, ...Evidence[]];
}
export interface FallbackItem {
  policy_id: string;
  amount_in_period_won?: number | null;
  reason?: string;
  amount_breakdown?: AmountBreakdown;
}
