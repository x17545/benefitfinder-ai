import { describe, expect, it } from 'vitest';
import { EMPTY_ANSWERS } from '../src/features/profile/flowStore.ts';
import type { ConditionAnswers } from '../src/features/profile/flowStore.ts';
import {
  answersFromProfile,
  loadSavedConditions,
  SAVED_CONDITIONS_KEY,
  saveConditions,
} from '../src/features/profile/savedConditions.ts';
import type { KeyValueStorage } from '../src/features/profile/savedConditions.ts';
import { toRecommendationRequest } from '../src/features/profile/toRequest.ts';

function memoryStorage(): KeyValueStorage & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return {
    data,
    getItem: (key) => data.get(key) ?? null,
    setItem: (key, value) => {
      data.set(key, value);
    },
    removeItem: (key) => {
      data.delete(key);
    },
  };
}

const ANSWERS: ConditionAnswers = {
  ...EMPTY_ANSWERS,
  age: 36,
  military: 'from_19_to_24',
  sidoCode: '41',
  regionCode: '41110',
  employmentStatus: 'job_seeker',
  school: 'university_graduate',
  marital: 'single',
  living: 'alone',
  homeOwnership: 'homeless',
  householdHead: 'head',
  contractType: 'monthly_rent',
  depositBand: 'under_50m',
  rentBand: 'between_40_60',
  incomeBand: 'none',
  assetBand: 'under_122m',
  special: ['none'],
  interests: ['employment', 'housing'],
  received: ['none'],
  householdSize: 2,
  parentIncome: 'between_3m_5m',
  parentAsset: null,
};

const SAVED_AT = new Date('2026-10-01T00:00:00Z');

describe('saved conditions (F09)', () => {
  it('saves the request profile and reads the same answers back', () => {
    const storage = memoryStorage();
    saveConditions(toRecommendationRequest(ANSWERS).profile, storage, SAVED_AT);

    const raw: unknown = JSON.parse(storage.data.get(SAVED_CONDITIONS_KEY) ?? 'null');
    expect(raw).toMatchObject({ saved_at: '2026-10-01T00:00:00.000Z', conditions: { region_code: '41110' } });

    const loaded = loadSavedConditions(storage, new Date('2026-10-20T00:00:00Z'));
    expect(loaded?.answers).toEqual(ANSWERS);
  });

  it('removes the saved value after 30 days', () => {
    const storage = memoryStorage();
    saveConditions(toRecommendationRequest(ANSWERS).profile, storage, SAVED_AT);
    expect(loadSavedConditions(storage, new Date('2026-11-01T00:00:01Z'))).toBe(null);
    expect(storage.data.has(SAVED_CONDITIONS_KEY)).toBe(false);
  });

  it('removes a broken value', () => {
    const storage = memoryStorage();
    storage.setItem(SAVED_CONDITIONS_KEY, '{not json');
    expect(loadSavedConditions(storage, SAVED_AT)).toBe(null);
    expect(storage.data.has(SAVED_CONDITIONS_KEY)).toBe(false);
  });

  it('drops values that are not in the choice lists', () => {
    const answers = answersFromProfile({ age: 25, employment_status: 'astronaut', domain_data: { special: ['none', 'x'] } });
    expect(answers?.employmentStatus).toBe(null);
    expect(answers?.special).toEqual(['none']);
    expect(answers?.parentsSkipped).toBe(true);
  });
});
