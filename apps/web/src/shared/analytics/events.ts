import type { AnalyticsEvent, AnalyticsEventRequest } from '../api/generated/analytics-event.ts';

export type { AnalyticsEvent };
type Value = string | number | boolean | null;
type Rule = (value: unknown) => boolean;
type Fields = Record<string, Rule>;

const integer = (minimum: number, maximum: number): Rule => (value) =>
  typeof value === 'number' && Number.isSafeInteger(value) && value >= minimum && value <= maximum;
const choice = (...values: readonly Value[]): Rule => (value) => values.includes(value as Value);
const policyId: Rule = (value) => typeof value === 'string' && /^[A-Za-z0-9:_-]{1,100}$/.test(value);
const ERROR_CODES = [
  'http_error', 'invalid_request', 'unsupported_domain', 'not_found', 'method_not_allowed',
  'daily_limit_exceeded', 'dependency_unavailable', 'internal_error', 'calculation_limit_exceeded',
  'timeout', 'network_error', 'invalid_response', 'unknown',
] as const;

// Runtime allowlist: types alone cannot prevent accidental profile/object spreads.
const FIELDS: Record<AnalyticsEvent['name'], Fields> = {
  consent_check: {}, remember_on: {},
  step_view: { step: integer(1, 13) }, step_complete: { step: integer(1, 13) },
  step_skip: {}, analyze_start: {},
  analyze_success: { duration_ms: integer(0, 120_000), portfolio_count: integer(0, 3) },
  analyze_fail: { code: choice(...ERROR_CODES) },
  period_change: { period: choice('1y', '2y', 'all') },
  pv_toggle: { on: (value) => typeof value === 'boolean' },
  combo_select: { rank: integer(1, 3) }, detail_view: { policy_id: policyId },
  apply_click: { policy_id: policyId, from: choice('result', 'detail'),
    rank: (value) => value === null || integer(1, 3)(value) },
  whatif_open: {},
  whatif_select: { scenario: choice('employed', 'income_up', 'independent', 'age_plus_one') },
  image_save: {}, clear_mine: {},
};

export function analyticsErrorCode(value: unknown): Extract<AnalyticsEvent, { name: 'analyze_fail' }>['code'] {
  return ERROR_CODES.find((code) => code === value) ?? 'unknown';
}

function safeEvent(input: unknown): AnalyticsEvent | null {
  if (input === null || typeof input !== 'object' || !('name' in input) ||
      typeof input.name !== 'string' || !Object.hasOwn(FIELDS, input.name)) return null;
  const fields = FIELDS[input.name as AnalyticsEvent['name']];
  if (Object.keys(input).some((key) => key !== 'name' && !Object.hasOwn(fields, key))) return null;
  const values: Record<string, Value> = { name: input.name };
  for (const [key, valid] of Object.entries(fields)) {
    const value = (input as Record<string, unknown>)[key];
    if (!valid(value)) return null;
    values[key] = value as Value;
  }
  return values as AnalyticsEvent;
}

let mode: 'disabled' | 'loading' | 'enabled' = 'disabled';
let identity: { client_id: string; session_id: number } | null = null;
let pending: AnalyticsEvent[] = [];
let initialization: Promise<void> | null = null;

function privacyRequested(): boolean {
  return typeof navigator !== 'undefined' && (navigator.doNotTrack === '1' ||
    ('globalPrivacyControl' in navigator && navigator.globalPrivacyControl === true));
}

function send(event: AnalyticsEvent): void {
  if (privacyRequested()) return;
  identity ??= { client_id: crypto.randomUUID(), session_id: Math.floor(Date.now() / 1000) };
  const request: AnalyticsEventRequest = { ...identity, event };
  // Same-origin only. No cookies, referrer, URL, profile, persistent identifier or retry queue.
  void fetch('/api/v1/analytics/events', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request), credentials: 'omit', referrerPolicy: 'no-referrer',
    keepalive: true, signal: AbortSignal.timeout(2000),
  }).catch(() => { /* Statistics must never interrupt the user's analysis. */ });
}

/** Called once at browser startup; missing/failed server configuration leaves collection off. */
export function initializeAnalytics(): Promise<void> {
  if (initialization !== null) return initialization;
  if (typeof window === 'undefined' || privacyRequested()) return Promise.resolve();
  mode = 'loading';
  initialization = (async () => {
    try {
      const response = await fetch('/api/v1/analytics/config', {
        credentials: 'omit', referrerPolicy: 'no-referrer', signal: AbortSignal.timeout(2000),
      });
      const config: unknown = response.ok ? await response.json() : null;
      if (config !== null && typeof config === 'object' && 'enabled' in config &&
          config.enabled === true && Object.keys(config).length === 1 && !privacyRequested()) {
        mode = 'enabled';
        const queued = pending;
        pending = [];
        for (const event of queued) send(event);
        return;
      }
    } catch {
      // No logging of responses or incoming data.
    }
    mode = 'disabled';
    pending = [];
  })();
  return initialization;
}

/** Only the specification's 17 events and exact scalar fields can cross this boundary. */
export function trackEvent(event: AnalyticsEvent): void {
  try {
    if (typeof window === 'undefined' || privacyRequested()) return;
    const safe = safeEvent(event);
    if (safe === null) return;
    if (mode === 'enabled') send(safe);
    else if (mode === 'loading' && pending.length < 32) pending.push(safe);
  } catch {
    // Unsupported crypto/network APIs also leave product behavior intact.
  }
}

/** Clearing local information also discards unsent events and the in-memory session identity. */
export function resetAnalyticsSession(): void {
  identity = null;
  pending = [];
}
