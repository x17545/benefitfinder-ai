import type { InKindService } from './generated/service.ts';
import { isDate, isHttpUrl, isNonEmptyString, isRecord } from './responseValidation.ts';

function isSafeWon(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
}

/** Verify the server result without deriving a display value from incomplete evidence. */
export function isService(value: unknown): value is InKindService {
  if (!isRecord(value) || !isNonEmptyString(value.item) || !isNonEmptyString(value.unit) ||
    !isSafeWon(value.quantity) || value.quantity < 1) return false;
  const { unit_value_won, value_basis, value_source_url, value_checked_at, estimated_value_won } = value;
  if (value.value_status === 'unknown') {
    return unit_value_won === null && value_basis === null && value_source_url === null &&
      value_checked_at === null && estimated_value_won === null;
  }
  return value.value_status === 'verified' && isSafeWon(unit_value_won) &&
    typeof value_basis === 'string' && value_basis.trim() !== '' &&
    typeof value_source_url === 'string' && /^https?:\/\/[^/?#\s]+/.test(value_source_url) &&
    isHttpUrl(value_source_url) && isDate(value_checked_at) &&
    isSafeWon(estimated_value_won) && estimated_value_won === unit_value_won * value.quantity;
}
