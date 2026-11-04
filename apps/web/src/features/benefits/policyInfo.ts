import { useEffect, useState } from 'react';
import { fetchBenefit } from '../../shared/api/benefits.ts';
import type { NormalizedBenefit } from '../../shared/api/benefits.ts';

/**
 * 추천 결과에는 policy_id만 있어서, 이름·기관·모집 상태는 정책 상세(GET /api/v1/benefits/{id})에서 받는다.
 * 한 번 받은 정책은 다시 묻지 않는다.
 */
const cache = new Map<string, Promise<NormalizedBenefit | null>>();

function load(id: string): Promise<NormalizedBenefit | null> {
  let pending = cache.get(id);
  if (pending === undefined) {
    pending = fetchBenefit(id).catch(() => {
      cache.delete(id); // 실패하면 다음에 다시 시도한다
      return null;
    });
    cache.set(id, pending);
  }
  return pending;
}

/** id → 정책 상세. 아직 못 받았거나 실패한 정책은 없다 */
export function usePolicyInfo(ids: readonly string[]): ReadonlyMap<string, NormalizedBenefit> {
  const [info, setInfo] = useState<ReadonlyMap<string, NormalizedBenefit>>(new Map());
  const key = [...new Set(ids)].sort().join('|');

  useEffect(() => {
    let active = true;
    const list = key === '' ? [] : key.split('|');
    void Promise.all(list.map(load)).then((benefits) => {
      if (!active) {
        return;
      }
      const next = new Map<string, NormalizedBenefit>();
      benefits.forEach((benefit, index) => {
        if (benefit !== null) {
          next.set(list[index], benefit);
        }
      });
      setInfo(next);
    });
    return () => {
      active = false;
    };
  }, [key]);

  return info;
}
