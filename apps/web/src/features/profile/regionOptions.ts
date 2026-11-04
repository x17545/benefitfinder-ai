import { useEffect, useState } from 'react';
import { fetchSelectOptions } from '../../shared/api/options.ts';
import type { SelectOptions } from '../../shared/api/options.ts';

export type Region = SelectOptions['regions'][number];

export type RegionOptionsState =
  | { status: 'loading' }
  | { status: 'ready'; regions: Region[]; isSample: boolean }
  | { status: 'error' };

/**
 * 개발 서버(pnpm dev)에서 서버의 지역 목록을 못 받을 때만 쓰는 가상 목록.
 * contracts/fixtures/options.json과 같은 값이며, 실제 서비스(빌드)에서는 쓰지 않는다.
 */
const DEV_SAMPLE_REGIONS: Region[] = [
  { sido_code: '11', sido_name: '[가상] 서울', sigungu: [{ code: '11110', name: '[가상] 종로구' }] },
  { sido_code: '41', sido_name: '[가상] 경기', sigungu: [{ code: '41110', name: '[가상] 수원시' }] },
];

let loaded: { regions: Region[]; isSample: boolean } | null = null;

async function loadRegions(signal: AbortSignal): Promise<{ regions: Region[]; isSample: boolean }> {
  if (loaded !== null) {
    return loaded;
  }
  try {
    const options = await fetchSelectOptions({ signal });
    loaded = { regions: options.regions, isSample: false };
  } catch (error) {
    if (signal.aborted || !import.meta.env.DEV) {
      throw error;
    }
    loaded = { regions: DEV_SAMPLE_REGIONS, isSample: true };
  }
  return loaded;
}

/** 2단계 지역 목록. 한 번 받으면 다시 묻지 않는다 */
export function useRegionOptions(): { state: RegionOptionsState; retry: () => void } {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<RegionOptionsState>({ status: 'loading' });

  useEffect(() => {
    const controller = new AbortController();
    loadRegions(controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) {
          setState({ status: 'ready', ...result });
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setState({ status: 'error' });
        }
      });
    return () => {
      controller.abort();
    };
  }, [attempt]);

  function retry(): void {
    setState({ status: 'loading' });
    setAttempt((value) => value + 1);
  }

  return { state, retry };
}
