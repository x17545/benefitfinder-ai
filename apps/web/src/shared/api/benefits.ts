import type { NormalizedBenefit } from './generated/benefit.ts';
import { BENEFITS_PATH } from '../config/api.ts';

export type { NormalizedBenefit };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

/** 정책 상세가 약속(contracts/v1 benefit)의 필수 모양인지 확인한다 */
export function isNormalizedBenefit(value: unknown): value is NormalizedBenefit {
  return (
    isRecord(value) &&
    typeof value.id === 'string' &&
    typeof value.title === 'string' &&
    typeof value.provider === 'string' &&
    Array.isArray(value.categories) &&
    isRecord(value.application) &&
    typeof value.application.status === 'string' &&
    isRecord(value.source) &&
    typeof value.source.url === 'string'
  );
}

export interface FetchBenefitOptions {
  signal?: AbortSignal;
  /** 테스트에서 바꿔 끼우는 fetch */
  fetchFn?: typeof fetch;
}

/** GET /api/v1/benefits/{benefit_id} */
export async function fetchBenefit(id: string, options: FetchBenefitOptions = {}): Promise<NormalizedBenefit> {
  const { signal, fetchFn = fetch } = options;
  const response = await fetchFn(`${BENEFITS_PATH}/${encodeURIComponent(id)}`, { signal });
  if (!response.ok) {
    throw new Error(`benefit request failed: HTTP ${response.status.toString()}`);
  }
  const body: unknown = await response.json();
  if (!isNormalizedBenefit(body)) {
    throw new Error('benefit request failed: unexpected response shape');
  }
  return body;
}
