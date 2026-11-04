import type { JSX } from 'react';
import type { Choice } from '../../../domains/youth/conditionOptions.ts';
import { ChoiceChip, ChoiceGroup } from '../../../shared/ui/ChoiceChip.tsx';

interface ChipQuestionProps<T extends string | number> {
  /** 작은 질문. 예: 주민등록상 세대주인가요? */
  title: string;
  description?: string;
  choices: readonly Choice<T>[];
  value: T | null;
  onChange: (value: T) => void;
}

/** 한 화면 안의 작은 질문 하나 (칩에서 하나 고르기) */
export function ChipQuestion<T extends string | number>({
  title,
  description,
  choices,
  value,
  onChange,
}: ChipQuestionProps<T>): JSX.Element {
  return (
    <section className="s2-sub">
      <h2 className="s2-sub__title">{title}</h2>
      {description !== undefined ? <p className="s2-sub__desc">{description}</p> : null}
      <ChoiceGroup label={title}>
        {choices.map((choice) => (
          <ChoiceChip
            key={choice.value}
            selected={value === choice.value}
            onSelect={() => {
              onChange(choice.value);
            }}
          >
            {choice.label}
          </ChoiceChip>
        ))}
      </ChoiceGroup>
    </section>
  );
}
