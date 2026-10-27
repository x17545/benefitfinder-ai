/* Generated from contracts/v1. Run pnpm types:generate; do not edit. */

/**
 * GET /health liveness response. Liveness only; it does not guarantee DB readiness.
 */
export interface HealthResponse {
  status: 'ok';
}
