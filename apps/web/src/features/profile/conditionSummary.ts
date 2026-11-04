import { EMPLOYMENT_CHOICES } from '../../domains/youth/conditionOptions.ts';
import type { Living } from '../../domains/youth/conditionOptions.ts';
import type { ConditionAnswers } from './flowStore.ts';

const LIVING_SENTENCE: Record<Living, string> = {
  alone: '혼자 살아요',
  with_parents: '부모님과 함께 살아요',
  with_family: '배우자·가족과 살아요',
  dorm: '기숙사·고시원에 살아요',
  other: '기타',
};

/** "만 25세 · 경기도 수원시 · 취업 준비 · 혼자 살아요" (S1 지난 조건, S3 조건 요약) */
export function conditionSummary(answers: ConditionAnswers): string {
  const parts = [
    answers.age !== null ? `만 ${String(answers.age)}세` : null,
    answers.regionLabel,
    EMPLOYMENT_CHOICES.find((choice) => choice.value === answers.employmentStatus)?.label ?? null,
    answers.living !== null ? LIVING_SENTENCE[answers.living] : null,
  ];
  return parts.filter((part) => part !== null).join(' · ');
}
