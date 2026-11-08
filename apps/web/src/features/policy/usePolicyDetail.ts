import { useCallback, useEffect, useState } from 'react';
import { isNormalizedBenefit } from '../../shared/api/benefits.ts';
import type { NormalizedBenefit } from '../../shared/api/benefits.ts';
import { BENEFITS_PATH } from '../../shared/config/api.ts';
import { devSampleBenefit } from './devSampleBenefits.ts';

export type PolicyDetailState =
  | { status: 'loading' }
  /** isSample: 개발용 가상 정책 상세로 본 것 */
  | { status: 'ok'; benefit: NormalizedBenefit; isSample: boolean }
  /** 없는 정책 ID (문구 S5-404) */
  | { status: 'notFound' }
  | { status: 'error' };

type LoadResult = { status: 'ok'; benefit: NormalizedBenefit } | { status: 'notFound' } | { status: 'error' };

/** GET /api/v1/benefits/{id}. 404는 없는 정책, 나머지 실패는 다시 시도할 수 있는 오류 */
export async function loadPolicyDetail(
  id: string,
  fetchFn: typeof fetch = fetch,
  signal?: AbortSignal,
): Promise<LoadResult> {
  try {
    const response = await fetchFn(`${BENEFITS_PATH}/${encodeURIComponent(id)}`, { signal });
    if (response.status === 404) {
      return { status: 'notFound' };
    }
    if (!response.ok) {
      return { status: 'error' };
    }
    const body: unknown = await response.json();
    return isNormalizedBenefit(body) ? { status: 'ok', benefit: body } : { status: 'error' };
  } catch {
    return { status: 'error' };
  }
}

/** 받은 결과를 화면 상태로. 개발 서버에서 API가 없으면 가상 예시를 쓴다 */
export function toDetailState(id: string, loaded: LoadResult): PolicyDetailState {
  if (loaded.status === 'ok') {
    return { status: 'ok', benefit: loaded.benefit, isSample: false };
  }
  const sample = devSampleBenefit(id);
  return sample !== null ? { status: 'ok', benefit: sample, isSample: true } : loaded;
}

/** S5에서 정책 상세를 받는다. 정책 ID나 [다시 시도]가 바뀌면 새로 받는다 */
export function usePolicyDetail(id: string): { state: PolicyDetailState; retry: () => void } {
  const [attempt, setAttempt] = useState(0);
  const [loaded, setLoaded] = useState<{ key: string; state: PolicyDetailState } | null>(null);
  const key = `${id}#${String(attempt)}`;

  useEffect(() => {
    const controller = new AbortController();
    void loadPolicyDetail(id, fetch, controller.signal).then((result) => {
      if (!controller.signal.aborted) {
        setLoaded({ key, state: toDetailState(id, result) });
      }
    });
    return () => {
      controller.abort();
    };
  }, [id, key]);

  const retry = useCallback(() => {
    setAttempt((count) => count + 1);
  }, []);

  return { state: loaded?.key === key ? loaded.state : { status: 'loading' }, retry };
}
