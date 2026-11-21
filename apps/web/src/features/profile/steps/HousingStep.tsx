import type { JSX } from 'react';
import {
  HOME_OWNERSHIP_CHOICES,
  HOUSEHOLD_HEAD_CHOICES,
  LIVING_CHOICES,
} from '../../../domains/youth/conditionOptions.ts';
import { TERM_HELP } from '../../../domains/youth/termHelp.ts';
import { ChoiceChip, ChoiceGroup } from '../../../shared/ui/ChoiceChip.tsx';
import { ConditionStepLayout } from '../ConditionStepLayout.tsx';
import { updateAnswers, useFlowState } from '../flowStore.ts';
import { questionElementId } from '../stepQuestions.ts';
import { ChipQuestion } from './ChipQuestion.tsx';

/** S2-6 사는 형태 + 집 소유 + 세대주 (3개 모두 필수) */
export function HousingStep(): JSX.Element {
  const { living, homeOwnership, householdHead } = useFlowState().answers;

  return (
    <ConditionStepLayout
      step={6}
      title="어떻게 살고 있나요?"
      canNext={living !== null && homeOwnership !== null && householdHead !== null}
      questions={[
        { id: 'living', answered: living !== null },
        { id: 'homeOwnership', answered: homeOwnership !== null },
        { id: 'householdHead', answered: householdHead !== null },
      ]}
    >
      <div id={questionElementId('living')}>
        <ChoiceGroup label="사는 형태" layout="rows">
          {LIVING_CHOICES.map((choice) => (
            <ChoiceChip
              key={choice.value}
              layout="row"
              selected={living === choice.value}
              onSelect={() => {
                updateAnswers({ living: choice.value });
              }}
            >
              {choice.label}
            </ChoiceChip>
          ))}
        </ChoiceGroup>
      </div>

      <ChipQuestion
        title="내 이름(또는 배우자)으로 된 집이 있나요?"
        description="분양권·입주권도 포함이에요"
        questionId="homeOwnership"
        help={TERM_HELP.homeOwnership}
        choices={HOME_OWNERSHIP_CHOICES}
        value={homeOwnership}
        onChange={(value) => {
          updateAnswers({ homeOwnership: value });
        }}
      />

      <ChipQuestion
        title="주민등록상 세대주인가요?"
        questionId="householdHead"
        help={TERM_HELP.householdHead}
        choices={HOUSEHOLD_HEAD_CHOICES}
        value={householdHead}
        onChange={(value) => {
          updateAnswers({ householdHead: value });
        }}
      />
    </ConditionStepLayout>
  );
}
