import type { JSX } from 'react';
import { navigate, paths } from '../../../app/router.ts';
import {
  HOUSEHOLD_SIZE_CHOICES,
  PARENT_ASSET_CHOICES,
  PARENT_INCOME_CHOICES,
} from '../../../domains/youth/conditionOptions.ts';
import { ConditionStepLayout } from '../ConditionStepLayout.tsx';
import { updateAnswers, useFlowState } from '../flowStore.ts';
import { ChipQuestion } from './ChipQuestion.tsx';

const PARENT_AGE_NOTE_FROM = 30;

/** S2-12 부모님 정보 (선택). 기혼이면 질문 대신 안내만 */
export function ParentsStep(): JSX.Element {
  const { age, marital, householdSize, parentIncome, parentAsset } = useFlowState().answers;
  const married = marital === 'married';

  function skip(): void {
    updateAnswers({ parentsSkipped: true, householdSize: null, parentIncome: null, parentAsset: null });
    navigate(paths.conditions(13));
  }

  if (married) {
    return (
      <ConditionStepLayout
        step={12}
        title="더 정확하게 볼까요?"
        description="부모님 정보 입력은 건너뛰어요. 정책별 부모님 기준은 따로 확인해요."
        canNext
      >
        {null}
      </ConditionStepLayout>
    );
  }

  const description =
    "일부 정책은 부모님(원가구) 소득·재산을 봐요. 모르면 '확인 필요'로 표시돼요" +
    (age !== null && age >= PARENT_AGE_NOTE_FROM ? '. 만 30세 이상이면 안 보는 정책도 있어요' : '');

  return (
    <ConditionStepLayout
      step={12}
      title="더 정확하게 볼까요?"
      description={description}
      canNext
      topExtra={
        <button type="button" className="s2-skip" onClick={skip}>
          건너뛰기
        </button>
      }
    >
      <ChipQuestion
        title="가구원 수"
        choices={HOUSEHOLD_SIZE_CHOICES}
        value={householdSize}
        onChange={(value) => {
          updateAnswers({ householdSize: value, parentsSkipped: false });
        }}
      />
      <ChipQuestion
        title="부모님 집 한 달 소득"
        description="세전, 가구 전체"
        choices={PARENT_INCOME_CHOICES}
        value={parentIncome}
        onChange={(value) => {
          updateAnswers({ parentIncome: value, parentsSkipped: false });
        }}
      />
      <ChipQuestion
        title="부모님 재산"
        choices={PARENT_ASSET_CHOICES}
        value={parentAsset}
        onChange={(value) => {
          updateAnswers({ parentAsset: value, parentsSkipped: false });
        }}
      />
    </ConditionStepLayout>
  );
}
