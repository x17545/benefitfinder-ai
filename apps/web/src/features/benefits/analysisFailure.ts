import { RecommendationError } from '../../shared/api/recommendations.ts';

/**
 * 분석 실패 이유 (L-E1·L-E2).
 * limit: 하루 상한, unavailable: 서버의 정책 자료를 쓸 수 없음(503 등),
 * network: 인터넷 연결 실패, error: 그 밖의 실패와 시간 초과
 */
export type AnalysisFailureReason = 'error' | 'limit' | 'unavailable' | 'network';

export function failureReason(error: unknown, timedOut: boolean): AnalysisFailureReason {
  if (timedOut || !(error instanceof RecommendationError)) {
    return 'error';
  }
  switch (error.code) {
    case 'daily_limit_exceeded':
      return 'limit';
    case 'dependency_unavailable':
      return 'unavailable';
    case 'network_error':
      return 'network';
    default:
      return 'error';
  }
}

export interface FailureCopy {
  title: string;
  description: string;
}

const KEEP = '고른 조건은 그대로 남아 있어요.';

/** 로딩 실패 화면 문구. limit은 화면 구성이 달라 따로 그린다 */
export const FAILURE_COPY: Record<Exclude<AnalysisFailureReason, 'limit'>, FailureCopy> = {
  unavailable: {
    title: '지금은 정책 자료를 불러올 수 없어요',
    description: `서비스 쪽 문제로 잠시 분석할 수 없어요. 입력한 조건 문제는 아니에요. 잠시 후 다시 시도해 주세요. ${KEEP}`,
  },
  network: {
    title: '인터넷 연결이 원활하지 않아요',
    description: `연결 상태를 확인한 뒤 다시 시도해 주세요. ${KEEP}`,
  },
  error: {
    title: '분석에 실패했어요',
    description: `잠시 후 다시 시도해 주세요. ${KEEP}`,
  },
};

/** limit을 뺀 실패 이유의 문구 (limit이 넘어오면 일반 실패 문구) */
export function failureCopy(reason: AnalysisFailureReason): FailureCopy {
  return reason === 'limit' ? FAILURE_COPY.error : FAILURE_COPY[reason];
}
