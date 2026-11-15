// Development-only extra facts for cash-like policies. Module memory only.
// Never written to localStorage, sessionStorage, saved conditions, or URLs.
// Attached as domain_data.cash_facts only for DEV + VITE_LOCAL_CASH_POLICY_INPUT=1
// for the current district. The cash_facts shape lives in contracts/v1 and
// the generated youth-profile type; this file only gates and holds the values.
import type { YouthProfileExtension } from '../../shared/api/generated/youth-profile.ts';

export type CashFacts = NonNullable<NonNullable<YouthProfileExtension['domain_data']>['cash_facts']>;

/** True only on the dev server with the local input flag on. */
export function isLocalCashPolicyInputEnabled(): boolean {
  return import.meta.env.DEV && import.meta.env.VITE_LOCAL_CASH_POLICY_INPUT === '1';
}

let memory: CashFacts | null = null;
let memoryRegion: string | null = null;

/** Facts held for that region, or null when disabled, unsupported, or another region. */
export function getCashFactsForRegion(regionCode: string | null): CashFacts | null {
  return isLocalCashPolicyInputEnabled() && regionCode !== null && memoryRegion === regionCode
    ? memory
    : null;
}

export function setCashFactsForRegion(regionCode: string, facts: CashFacts | null): void {
  memoryRegion = regionCode;
  memory = facts;
}

export function clearCashFacts(): void {
  memory = null;
  memoryRegion = null;
}
