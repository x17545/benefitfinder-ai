import type { JSX } from 'react';
import {
  AGE_NOTICE_MAX,
  AGE_NOTICE_MIN,
  MILITARY_CHOICES,
  MILITARY_QUESTION_MIN_AGE,
} from '../../../domains/youth/conditionOptions.ts';
import { ChoiceChip, ChoiceGroup } from '../../../shared/ui/ChoiceChip.tsx';
import { ConditionStepLayout } from '../ConditionStepLayout.tsx';
import { updateAnswers, useFlowState } from '../flowStore.ts';

/** S2-1 나이 (+ 35세 이상이면 군 복무) */
export function AgeStep(): JSX.Element {
  const { age, military } = useFlowState().answers;

  const showMilitary = age !== null && age >= MILITARY_QUESTION_MIN_AGE;
  const outOfRange = age !== null && (age < AGE_NOTICE_MIN || age > AGE_NOTICE_MAX);
  const canNext = age !== null && (!showMilitary || military !== null);

  return (
    <ConditionStepLayout step={1} title="나이가 어떻게 되나요?" description="만 나이로 적어 주세요" canNext={canNext}>
      <div className="s2-number">
        <input
          type="text"
          inputMode="numeric"
          autoComplete="off"
          aria-label="만 나이"
          value={age === null ? '' : String(age)}
          onChange={(event) => {
            // 숫자가 아닌 글자는 지운다 (기획서 4.3)
            const digits = event.target.value.replace(/\D/g, '').slice(0, 3);
            updateAnswers({ age: digits === '' ? null : Number(digits) });
          }}
        />
        <span aria-hidden="true">세</span>
      </div>

      {outOfRange ? (
        <p className="s2-warn">
          <b aria-hidden="true">! </b>
          청년(만 19 ~ 39세) 정책 위주라 받을 수 있는 게 적을 수 있어요. 그래도 계속 볼 수 있어요.
        </p>
      ) : null}

      {showMilitary ? (
        <section className="s2-sub">
          <h2 className="s2-sub__title">군 복무를 마쳤나요?</h2>
          <p className="s2-sub__desc">
            복무한 기간만큼 나이 기준을 늘려 주는 정책이 있어요 (연장 폭과 상한은 정책 공고마다 다르고, 공고에
            없으면 확인 필요로 표시해요)
          </p>
          <ChoiceGroup label="군 복무 기간">
            {MILITARY_CHOICES.map((choice) => (
              <ChoiceChip
                key={choice.value}
                selected={military === choice.value}
                onSelect={() => {
                  updateAnswers({ military: choice.value });
                }}
              >
                {choice.label}
              </ChoiceChip>
            ))}
          </ChoiceGroup>
        </section>
      ) : null}
    </ConditionStepLayout>
  );
}
