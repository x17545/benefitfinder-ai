import { useEffect, useRef, useState } from 'react';
import { fetchSelectOptions } from '../../shared/api/options.ts';
import type { SelectOptions } from '../../shared/api/options.ts';

export type Region = SelectOptions['regions'][number];

export type RegionOptionsState =
  | { status: 'loading' }
  | { status: 'ready'; regions: Region[] }
  | { status: 'empty' }
  | { status: 'error' };

/**
 * 실패하면 화면에 오류를 띄우기 전에 조용히 다시 받아 보는 간격(ms).
 * 서버가 막 켜졌거나 공식 목록을 늦게 받아 오는 순간의 일시 실패를 사용자가 직접 누르지 않아도 넘긴다.
 */
export const REGION_AUTO_RETRY_DELAYS_MS: readonly number[] = [1_500, 3_000];

/** Step-2 region catalog: fresh fetch per mount, retry refetches. No module cache. */
export function useRegionOptions(): { state: RegionOptionsState; retry: () => void } {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<RegionOptionsState>({ status: 'loading' });
  const autoRetries = useRef(0);

  useEffect(() => {
    const controller = new AbortController();
    let timer: number | undefined;
    fetchSelectOptions({ signal: controller.signal })
      .then((options) => {
        if (controller.signal.aborted) {
          return;
        }
        if (options.regions.length === 0) {
          setState({ status: 'empty' });
        } else {
          setState({ status: 'ready', regions: options.regions });
        }
      })
      .catch(() => {
        if (controller.signal.aborted) {
          return;
        }
        const delay = REGION_AUTO_RETRY_DELAYS_MS[autoRetries.current];
        if (delay !== undefined) {
          // 오류 대신 '불러오고 있어요'를 유지한 채 잠시 뒤 다시 받는다.
          autoRetries.current += 1;
          timer = window.setTimeout(() => {
            setAttempt((value) => value + 1);
          }, delay);
          return;
        }
        setState({ status: 'error' });
      });
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [attempt]);

  function retry(): void {
    autoRetries.current = 0;
    setState({ status: 'loading' });
    setAttempt((value) => value + 1);
  }

  return { state, retry };
}
