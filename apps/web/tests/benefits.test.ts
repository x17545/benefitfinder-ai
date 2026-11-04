import { describe, expect, it } from 'vitest';
import { fetchBenefit, isNormalizedBenefit } from '../src/shared/api/benefits.ts';
import fixtureText from '../../../contracts/fixtures/youth-benefit.json?raw';

const parsed: unknown = JSON.parse(fixtureText);
if (!isNormalizedBenefit(parsed)) { throw new Error('invalid synthetic benefit'); }
const BENEFIT = parsed;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

describe('policy detail validation', () => {
  it('accepts the synthetic contract fixture', async () => {
    await expect(fetchBenefit(BENEFIT.id, { fetchFn: async () => json(BENEFIT) })).resolves.toEqual(BENEFIT);
  });

  it('accepts missing optional extensions, empty conflicts and empty eligibility conditions', () => {
    const value = structuredClone(BENEFIT);
    delete value.benefit_amount;
    delete value.reviewed;
    delete value.conflicts;
    delete value.lifetime_once;
    value.eligibility_conditions = [];
    expect(isNormalizedBenefit(value)).toBe(true);
    value.conflicts = [];
    expect(isNormalizedBenefit(value)).toBe(true);
  });

  it('accepts explicit nullable extensions and unknown application dates', () => {
    expect(isNormalizedBenefit({
      ...BENEFIT, benefit_amount: null, reviewed: null, conflicts: null, lifetime_once: null,
      application: { status: 'unknown', starts_on: null, ends_on: null, url: null },
    })).toBe(true);
    expect(isNormalizedBenefit({ ...BENEFIT, benefit_amount: { amount_text: null, period_months: null } })).toBe(true);
  });

  it.each([
    { name: 'conflicts object', body: { ...BENEFIT, conflicts: {} } },
    { name: 'null conflict item', body: { ...BENEFIT, conflicts: [null] } },
    { name: 'missing conflict policy id', body: { ...BENEFIT, conflicts: [{ evidence: [] }] } },
    { name: 'non-array conflict evidence', body: { ...BENEFIT, conflicts: [{ policy_id: 'youth:example:P1', evidence: {} }] } },
    { name: 'malformed conflict evidence', body: { ...BENEFIT, conflicts: [{ policy_id: 'youth:example:P1', evidence: [{}] }] } },
    { name: 'empty required conflict evidence', body: { ...BENEFIT, conflicts: [{ policy_id: 'youth:example:P1', evidence: [] }] } },
    { name: 'object amount text', body: { ...BENEFIT, benefit_amount: { amount_text: { amount: 20 } } } },
    { name: 'invalid amount', body: { ...BENEFIT, benefit_amount: { amount_won: -1 } } },
    { name: 'non-string category', body: { ...BENEFIT, categories: [null] } },
    { name: 'unsupported recruitment status', body: { ...BENEFIT, application: { ...BENEFIT.application, status: 'always' } } },
    { name: 'invalid application URL', body: { ...BENEFIT, application: { ...BENEFIT.application, url: { url: 'synthetic' } } } },
    { name: 'invalid source URL', body: { ...BENEFIT, source: { ...BENEFIT.source, url: 'javascript:alert(1)' } } },
    { name: 'missing source timestamp', body: { ...BENEFIT, source: { url: BENEFIT.source.url, document_version: 'synthetic' } } },
    { name: 'broken eligibility evidence', body: { ...BENEFIT, eligibility_conditions: [{ id: 'age', description: '가상 연령', evidence: [null] }] } },
  ])('rejects $name before the detail reaches UI or its cache', async ({ body }) => {
    expect(isNormalizedBenefit(body)).toBe(false);
    await expect(fetchBenefit(BENEFIT.id, { fetchFn: async () => json(body) })).rejects.toThrow('unexpected response shape');
  });

  it('checks application dates and preserves valid leap days', () => {
    expect(isNormalizedBenefit({ ...BENEFIT, application: { ...BENEFIT.application, ends_on: '2024-02-29' } })).toBe(true);
    expect(isNormalizedBenefit({ ...BENEFIT, application: { ...BENEFIT.application, ends_on: '2026-02-29' } })).toBe(false);
  });

  it('rejects non-2xx responses', async () => {
    await expect(fetchBenefit(BENEFIT.id, { fetchFn: async () => json(BENEFIT, 503) })).rejects.toThrow('HTTP 503');
  });
});
