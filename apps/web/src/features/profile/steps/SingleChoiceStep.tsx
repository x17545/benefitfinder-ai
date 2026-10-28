import type { JSX } from 'react';
import type { Choice } from '../../../domains/youth/conditionOptions.ts';
import { ChoiceChip, ChoiceGroup } from '../../../shared/ui/ChoiceChip.tsx';
import { ConditionStepLayout } from '../ConditionStepLayout.tsx';

interface SingleChoiceStepProps<T extends string> {
  step: number;
  title: string;
  description?: string;
  /** 화면 읽기 도구용 묶음 이름 */
  label: string;
  choices: readonly Choice<T>[];
  value: T | null;
  onChange: (value: T) => void;
}

/** 하나만 고르는 질문 (큰 목록). 3 · 4 · 5단계 */
export function SingleChoiceStep<T extends string>({
  step,
  title,
  description,
  label,
  choices,
  value,
  onChange,
}: SingleChoiceStepProps<T>): JSX.Element {
  return (
    <ConditionStepLayout step={step} title={title} description={description} canNext={value !== null}>
      <ChoiceGroup label={label} layout="rows">
        {choices.map((choice) => (
          <ChoiceChip
            key={choice.value}
            layout="row"
            selected={value === choice.value}
            onSelect={() => {
              onChange(choice.value);
            }}
          >
            {choice.label}
          </ChoiceChip>
        ))}
      </ChoiceGroup>
    </ConditionStepLayout>
  );
}
