import { useEffect, useState } from 'react';
import { BenefitNotFoundError, fetchBenefit } from '../../shared/api/benefits.ts';
import type { RecommendationResponse } from '../../shared/api/generated/recommendation-response.ts';
import { matchesServiceSnapshot } from './serviceModel.ts';

type Verification = 'current' | 'changed' | 'unavailable';

/** Recheck public details on entry without spending an analysis or sending conditions. */
export function useServiceSnapshot(result: RecommendationResponse | null): {
  status: 'checking' | Verification;
  retry: () => void;
} {
  const entries = result?.services;
  const [attempt, setAttempt] = useState(0);
  const [checked, setChecked] = useState<{
    result: RecommendationResponse;
    attempt: number;
    status: Verification;
  } | null>(null);

  useEffect(() => {
    if (result === null || entries === undefined || entries.length === 0) return undefined;
    const controller = new AbortController();
    const { signal } = controller;
    const current = result;

    function finish(status: Verification): void {
      if (signal.aborted) return;
      window.clearTimeout(deadline);
      setChecked({ result: current, attempt, status });
    }

    const deadline = window.setTimeout(() => {
      finish('unavailable');
      controller.abort();
    }, 60_000);

    void Promise.all(entries.map(async (entry): Promise<Verification> => {
      try {
        const latest = await fetchBenefit(entry.policy_id, { signal });
        return matchesServiceSnapshot(entry, latest) ? 'current' : 'changed';
      } catch (error) {
        return error instanceof BenefitNotFoundError ? 'changed' : 'unavailable';
      }
    })).then((statuses) => {
      finish(statuses.includes('changed') ? 'changed' : statuses.includes('unavailable') ? 'unavailable' : 'current');
    });

    return () => {
      window.clearTimeout(deadline);
      controller.abort();
    };
  }, [result, entries, attempt]);

  return {
    status: entries === undefined || entries.length === 0 ? 'current' :
      checked?.result === result && checked.attempt === attempt ? checked.status : 'checking',
    retry: () => { setAttempt((previous) => previous + 1); },
  };
}
