import type { JSX } from 'react';
import { ASSET_CHOICES, INCOME_CHOICES } from '../../../domains/youth/conditionOptions.ts';
import { ConditionStepLayout } from '../ConditionStepLayout.tsx';
import { updateAnswers, useFlowState } from '../flowStore.ts';
import { ChipQuestion } from './ChipQuestion.tsx';

/** S2-8 본인 소득과 재산 (둘 다 필수, 모름 가능) */
export function IncomeStep(): JSX.Element {
  const { incomeBand, assetBand } = useFlowState().answers;

  return (
    <ConditionStepLayout
      step={8}
      title="본인 소득과 재산은요?"
      description="구간만 골라요. 몰라도 괜찮아요"
      canNext={incomeBand !== null && assetBand !== null}
    >
      <ChipQuestion
        title="연 소득 (세전)"
        choices={INCOME_CHOICES}
        value={incomeBand}
        onChange={(value) => {
          updateAnswers({ incomeBand: value });
        }}
      />
      <ChipQuestion
        title="재산"
        description="예금·보증금·자동차 등을 더하고 대출은 빼요"
        choices={ASSET_CHOICES}
        value={assetBand}
        onChange={(value) => {
          updateAnswers({ assetBand: value });
        }}
      />
    </ConditionStepLayout>
  );
}
