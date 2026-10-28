import { useEffect } from 'react';
import type { JSX } from 'react';
import { navigate, paths } from '../../app/router.ts';
import {
  EMPLOYMENT_CHOICES,
  MARITAL_CHOICES,
  SCHOOL_CHOICES,
} from '../../domains/youth/conditionOptions.ts';
import { ConditionStepLayout } from './ConditionStepLayout.tsx';
import { updateAnswers, useFlowState } from './flowStore.ts';
import { AgeStep } from './steps/AgeStep.tsx';
import { SingleChoiceStep } from './steps/SingleChoiceStep.tsx';

interface ConditionScreenProps {
  step: number;
}

/** S2 조건 고르기 n단계. 주소 /conditions/n */
export function ConditionScreen({ step }: ConditionScreenProps): JSX.Element {
  const { consent, answers } = useFlowState();

  // S1 필수 동의 없이 주소로 바로 들어오면 S1로 돌려보낸다
  useEffect(() => {
    if (!consent) {
      navigate(paths.start, { replace: true });
    }
  }, [consent]);

  if (!consent) {
    return <></>;
  }

  switch (step) {
    case 1:
      return <AgeStep />;
    case 3:
      return (
        <SingleChoiceStep
          step={3}
          title="지금 상황은요?"
          description="가장 가까운 것 하나를 골라 주세요"
          label="지금 상황"
          choices={EMPLOYMENT_CHOICES}
          value={answers.employmentStatus}
          onChange={(value) => {
            updateAnswers({ employmentStatus: value });
          }}
        />
      );
    case 4:
      return (
        <SingleChoiceStep
          step={4}
          title="최종 학력은요?"
          label="최종 학력"
          choices={SCHOOL_CHOICES}
          value={answers.school}
          onChange={(value) => {
            updateAnswers({ school: value });
          }}
        />
      );
    case 5:
      return (
        <SingleChoiceStep
          step={5}
          title="결혼했나요?"
          description="신혼부부 기준이 따로 있거나, 결혼하면 부모님 소득을 안 보는 정책이 있어요"
          label="결혼"
          choices={MARITAL_CHOICES}
          value={answers.marital}
          onChange={(value) => {
            updateAnswers({ marital: value });
          }}
        />
      );
    default:
      // 아직 만들지 않은 단계 (5단계 다음 묶음에서 만든다)
      return (
        <ConditionStepLayout step={step} title="이 단계는 다음 묶음에서 만들어요" canNext>
          <p className="s2-desc">개발 중인 자리예요. [다음]으로 넘어갈 수 있어요.</p>
        </ConditionStepLayout>
      );
  }
}
