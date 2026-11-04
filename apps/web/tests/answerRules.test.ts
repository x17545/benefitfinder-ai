import { describe, expect, it } from 'vitest';
import { missingSteps, toggleChoice } from '../src/features/profile/answerRules.ts';
import { EMPTY_ANSWERS } from '../src/features/profile/flowStore.ts';
import type { ConditionAnswers } from '../src/features/profile/flowStore.ts';

const COMPLETE: ConditionAnswers = {
  ...EMPTY_ANSWERS,
  age: 25,
  sidoCode: '41',
  regionCode: '41110',
  regionLabel: '경기도 수원시',
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
};

describe('toggleChoice', () => {
  it('adds and removes a value', () => {
    expect(toggleChoice(['a'], 'b')).toEqual(['a', 'b']);
    expect(toggleChoice(['a', 'b'], 'a')).toEqual(['b']);
  });

  it('keeps "none" alone', () => {
    expect(toggleChoice(['a', 'b'], 'none', 'none')).toEqual(['none']);
    expect(toggleChoice(['none'], 'a', 'none')).toEqual(['a']);
  });
});

describe('missingSteps', () => {
  it('is empty when every required step is answered', () => {
    expect(missingSteps(COMPLETE)).toEqual([]);
  });

  it('lists every required step when nothing is answered', () => {
    expect(missingSteps(EMPTY_ANSWERS)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]);
  });

  it('asks for military service from age 35', () => {
    expect(missingSteps({ ...COMPLETE, age: 35 })).toEqual([1]);
    expect(missingSteps({ ...COMPLETE, age: 35, military: 'none' })).toEqual([]);
  });

  it('needs the rent band only for monthly rent', () => {
    expect(missingSteps({ ...COMPLETE, rentBand: null })).toEqual([7]);
    expect(missingSteps({ ...COMPLETE, contractType: 'jeonse', rentBand: null })).toEqual([]);
    expect(missingSteps({ ...COMPLETE, contractType: 'owned', depositBand: null, rentBand: null })).toEqual([]);
  });
});
