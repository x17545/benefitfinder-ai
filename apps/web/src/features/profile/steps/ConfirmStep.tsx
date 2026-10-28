import type { JSX } from 'react';
import { navigate, paths } from '../../../app/router.ts';
import {
  ASSET_CHOICES,
  CONTRACT_CHOICES,
  DEPOSIT_CHOICES,
  EMPLOYMENT_CHOICES,
  HOME_OWNERSHIP_CHOICES,
  HOUSEHOLD_HEAD_CHOICES,
  HOUSEHOLD_SIZE_CHOICES,
  INCOME_CHOICES,
  INTEREST_CHOICES,
  LIVING_CHOICES,
  MARITAL_CHOICES,
  MILITARY_CHOICES,
  PARENT_ASSET_CHOICES,
  PARENT_INCOME_CHOICES,
  RECEIVED_CHOICES,
  RENT_CHOICES,
  SCHOOL_CHOICES,
  SPECIAL_CHOICES,
} from '../../../domains/youth/conditionOptions.ts';
import type { Choice } from '../../../domains/youth/conditionOptions.ts';
import { missingSteps, needsMilitary } from '../answerRules.ts';
import { ConditionStepLayout } from '../ConditionStepLayout.tsx';
import { useFlowState } from '../flowStore.ts';
import type { ConditionAnswers } from '../flowStore.ts';

interface SummaryRow {
  label: string;
  /** null이면 아직 안 고름 */
  value: string | null;
  /** 누르면 갈 단계 */
  step: number;
  /** 건너뛴 값처럼 흐리게 */
  muted?: boolean;
}

function labelOf<T>(choices: readonly Choice<T>[], value: T | null): string | null {
  return choices.find((choice) => choice.value === value)?.label ?? null;
}

function labelsOf<T>(choices: readonly Choice<T>[], values: readonly T[]): string | null {
  const labels = choices.filter((choice) => values.includes(choice.value)).map((choice) => choice.label);
  return labels.length > 0 ? labels.join(', ') : null;
}

function contractLabel(answers: ConditionAnswers): string | null {
  const type = labelOf(CONTRACT_CHOICES, answers.contractType);
  if (type === null) {
    return null;
  }
  const deposit = labelOf(DEPOSIT_CHOICES, answers.depositBand);
  const rent = labelOf(RENT_CHOICES, answers.rentBand);
  return [type, deposit !== null ? `보증금 ${deposit}` : null, rent !== null ? `월세 ${rent}` : null]
    .filter((part) => part !== null)
    .join(' · ');
}

function parentRow(label: string, value: string | null, skipped: boolean): SummaryRow {
  if (value === null) {
    return { label, value: '건너뜀', step: 12, muted: true };
  }
  return { label, value, step: 12, muted: skipped };
}

function buildRows(answers: ConditionAnswers): SummaryRow[] {
  const rows: SummaryRow[] = [
    { label: '나이', value: answers.age !== null ? `만 ${String(answers.age)}세` : null, step: 1 },
  ];
  if (needsMilitary(answers.age)) {
    rows.push({ label: '군 복무', value: labelOf(MILITARY_CHOICES, answers.military), step: 1 });
  }
  rows.push(
    { label: '지역', value: answers.regionLabel, step: 2 },
    { label: '지금 상황', value: labelOf(EMPLOYMENT_CHOICES, answers.employmentStatus), step: 3 },
    { label: '최종 학력', value: labelOf(SCHOOL_CHOICES, answers.school), step: 4 },
    { label: '결혼', value: labelOf(MARITAL_CHOICES, answers.marital), step: 5 },
    { label: '사는 형태', value: labelOf(LIVING_CHOICES, answers.living), step: 6 },
    { label: '집 소유', value: labelOf(HOME_OWNERSHIP_CHOICES, answers.homeOwnership), step: 6 },
    { label: '세대주', value: labelOf(HOUSEHOLD_HEAD_CHOICES, answers.householdHead), step: 6 },
    { label: '집 계약', value: contractLabel(answers), step: 7 },
    { label: '본인 소득', value: labelOf(INCOME_CHOICES, answers.incomeBand), step: 8 },
    { label: '본인 재산', value: labelOf(ASSET_CHOICES, answers.assetBand), step: 8 },
    { label: '해당 조건', value: labelsOf(SPECIAL_CHOICES, answers.special), step: 9 },
    { label: '관심 분야', value: labelsOf(INTEREST_CHOICES, answers.interests), step: 10 },
    { label: '받은 정책', value: labelsOf(RECEIVED_CHOICES, answers.received), step: 11 },
  );
  // 기혼이면 부모님 행 3개를 숨긴다 (기획서 4.3 S2-13)
  if (answers.marital !== 'married') {
    rows.push(
      parentRow('가구원 수', labelOf(HOUSEHOLD_SIZE_CHOICES, answers.householdSize), answers.parentsSkipped),
      parentRow('부모님 소득', labelOf(PARENT_INCOME_CHOICES, answers.parentIncome), answers.parentsSkipped),
      parentRow('부모님 재산', labelOf(PARENT_ASSET_CHOICES, answers.parentAsset), answers.parentsSkipped),
    );
  }
  return rows;
}

/** S2-13 확인. 항목을 누르면 그 단계로 간다 */
export function ConfirmStep(): JSX.Element {
  const { answers } = useFlowState();
  const rows = buildRows(answers);
  const complete = missingSteps(answers).length === 0;

  return (
    <ConditionStepLayout
      step={13}
      title="이 조건으로 찾을까요?"
      description="항목을 누르면 고칠 수 있어요"
      canNext={complete}
      nextLabel="내 조합 찾기"
    >
      <ul className="s2-summary ui-card">
        {rows.map((row) => (
          <li key={row.label}>
            <button
              type="button"
              className="s2-summary__row"
              onClick={() => {
                navigate(paths.conditions(row.step));
              }}
            >
              <span className="s2-summary__label">{row.label}</span>
              <span
                className={
                  row.value === null
                    ? 's2-summary__value s2-summary__value--missing'
                    : row.muted === true
                      ? 's2-summary__value s2-summary__value--muted'
                      : 's2-summary__value'
                }
              >
                {row.value ?? '골라 주세요'} <span aria-hidden="true">›</span>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </ConditionStepLayout>
  );
}
