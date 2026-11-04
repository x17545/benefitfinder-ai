import type { JSX, ReactNode } from 'react';
import type { Choice } from '../../../domains/youth/conditionOptions.ts';
import { ChoiceChip, ChoiceGroup } from '../../../shared/ui/ChoiceChip.tsx';
import { toggleChoice } from '../answerRules.ts';
import { ConditionStepLayout } from '../ConditionStepLayout.tsx';

interface MultiChoiceStepProps<T extends string> {
  step: number;
  title: string;
  description?: string;
  /** 화면 읽기 도구용 묶음 이름 */
  label: string;
  choices: readonly Choice<T>[];
  values: readonly T[];
  /** '해당 없음' 같은 단독 선택지. 고르면 나머지가 풀린다 */
  noneValue?: T;
  onChange: (values: T[]) => void;
  /** 선택지 아래 안내 */
  children?: ReactNode;
}

/** 여러 개 고르는 질문 (칩). 1개 이상 골라야 [다음]. 9 · 10 · 11단계 */
export function MultiChoiceStep<T extends string>({
  step,
  title,
  description,
  label,
  choices,
  values,
  noneValue,
  onChange,
  children,
}: MultiChoiceStepProps<T>): JSX.Element {
  return (
    <ConditionStepLayout step={step} title={title} description={description} canNext={values.length > 0}>
      <ChoiceGroup label={label} kind="checkbox">
        {choices.map((choice) => (
          <ChoiceChip
            key={choice.value}
            kind="checkbox"
            selected={values.includes(choice.value)}
            onSelect={() => {
              onChange(toggleChoice(values, choice.value, noneValue));
            }}
          >
            {choice.label}
          </ChoiceChip>
        ))}
      </ChoiceGroup>
      {children}
    </ConditionStepLayout>
  );
}
