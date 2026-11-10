import { useEffect, useState } from 'react';
import { fetchSelectOptions } from '../../shared/api/options.ts';
import type { SelectOptions } from '../../shared/api/options.ts';

export type Region = SelectOptions['regions'][number];

export type RegionOptionsState =
  | { status: 'loading' }
  | { status: 'ready'; regions: Region[] }
  | { status: 'empty' }
  | { status: 'error' };

/** Step-2 region catalog: fresh fetch per mount, retry refetches. No module cache. */
export function useRegionOptions(): { state: RegionOptionsState; retry: () => void } {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<RegionOptionsState>({ status: 'loading' });

  useEffect(() => {
    const controller = new AbortController();
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
