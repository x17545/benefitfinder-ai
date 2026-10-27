/* Generated from contracts/v1. Run pnpm types:generate; do not edit. */

export interface UserConfirmedProfile {
  age?: number | null;
  region_code?: string | null;
  employment_status?: string | null;
  income_band?: string | null;
  household_type?: string | null;
  domain_data?: {
    [k: string]: unknown;
  };
}
