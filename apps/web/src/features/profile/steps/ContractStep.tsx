import type { JSX } from 'react';
import { CONTRACT_CHOICES, DEPOSIT_CHOICES, RENT_CHOICES } from '../../../domains/youth/conditionOptions.ts';
import type { ContractType } from '../../../domains/youth/conditionOptions.ts';
import { ChoiceChip, ChoiceGroup } from '../../../shared/ui/ChoiceChip.tsx';
import { ConditionStepLayout } from '../ConditionStepLayout.tsx';
import { updateAnswers, useFlowState } from '../flowStore.ts';
import { ChipQuestion } from './ChipQuestion.tsx';

function needsDeposit(type: ContractType | null): boolean {
  return type === 'monthly_rent' || type === 'jeonse';
}

/** S2-7 집 계약. 월세·전세면 보증금, 월세면 월세 금액도 (보이는 것 모두 필수) */
export function ContractStep(): JSX.Element {
  const { contractType, depositBand, rentBand } = useFlowState().answers;

  const showDeposit = needsDeposit(contractType);
  const showRent = contractType === 'monthly_rent';
  const canNext =
    contractType !== null && (!showDeposit || depositBand !== null) && (!showRent || rentBand !== null);

  function selectType(type: ContractType): void {
    // 계약 종류가 바뀌면 더 이상 보이지 않는 값은 지운다
    updateAnswers({
      contractType: type,
      depositBand: needsDeposit(type) ? depositBand : null,
      rentBand: type === 'monthly_rent' ? rentBand : null,
    });
  }

  return (
    <ConditionStepLayout
      step={7}
      title="지금 사는 집 계약은요?"
      description="주거 정책은 보증금·월세 금액에 상한이 있어요"
      canNext={canNext}
    >
      <ChoiceGroup label="집 계약" layout="rows">
        {CONTRACT_CHOICES.map((choice) => (
          <ChoiceChip
            key={choice.value}
            layout="row"
            selected={contractType === choice.value}
            onSelect={() => {
              selectType(choice.value);
            }}
          >
            {choice.label}
          </ChoiceChip>
        ))}
      </ChoiceGroup>

      {showDeposit ? (
        <ChipQuestion
          title="보증금"
          choices={DEPOSIT_CHOICES}
          value={depositBand}
          onChange={(value) => {
            updateAnswers({ depositBand: value });
          }}
        />
      ) : null}

      {showRent ? (
        <ChipQuestion
          title="월세"
          choices={RENT_CHOICES}
          value={rentBand}
          onChange={(value) => {
            updateAnswers({ rentBand: value });
          }}
        />
      ) : null}
    </ConditionStepLayout>
  );
}
