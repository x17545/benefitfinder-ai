import { useEffect } from 'react';
import type { JSX } from 'react';
import { navigate, paths } from '../../app/router.ts';
import {
  EMPLOYMENT_CHOICES,
  INTEREST_CHOICES,
  MARITAL_CHOICES,
  RECEIVED_CHOICES,
  SCHOOL_CHOICES,
  SPECIAL_CHOICES,
} from '../../domains/youth/conditionOptions.ts';
import { updateAnswers, useFlowState } from './flowStore.ts';
import { AgeStep } from './steps/AgeStep.tsx';
import { ConfirmStep } from './steps/ConfirmStep.tsx';
import { ContractStep } from './steps/ContractStep.tsx';
import { HousingStep } from './steps/HousingStep.tsx';
import { IncomeStep } from './steps/IncomeStep.tsx';
import { MultiChoiceStep } from './steps/MultiChoiceStep.tsx';
import { ParentsStep } from './steps/ParentsStep.tsx';
import { RegionStep } from './steps/RegionStep.tsx';
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
    case 2:
      return <RegionStep />;
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
    case 6:
      return <HousingStep />;
    case 7:
      return <ContractStep />;
    case 8:
      return <IncomeStep />;
    case 9:
      return (
        <MultiChoiceStep
          step={9}
          title="해당하는 게 있나요?"
          description="이런 분만 받을 수 있는 정책이 따로 있어요. 여러 개 고를 수 있어요"
          label="해당 조건"
          choices={SPECIAL_CHOICES}
          values={answers.special}
          noneValue="none"
          onChange={(values) => {
            updateAnswers({ special: values });
          }}
        >
          <p className="s2-private">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <rect x="5" y="11" width="14" height="10" rx="2" />
              <path d="M8 11V8a4 4 0 0 1 8 0v3" />
            </svg>
            이 정보는 자격 계산에만 써요. 서버에 저장하지 않아요
          </p>
        </MultiChoiceStep>
      );
    case 10:
      return (
        <MultiChoiceStep
          step={10}
          title="어떤 지원에 관심이 있나요?"
          description="여러 개를 고를 수 있어요. 초기에는 취업·주거 혜택을 다뤄요"
          label="관심 지원"
          choices={INTEREST_CHOICES}
          values={answers.interests}
          onChange={(values) => {
            updateAnswers({ interests: values });
          }}
        />
      );
    case 11:
      return (
        <MultiChoiceStep
          step={11}
          title="이미 받고 있거나 받았던 게 있나요?"
          description="한 번만 받을 수 있거나 함께 가입할 수 없는 정책은 조합에서 빼요"
          label="받았던 지원"
          choices={RECEIVED_CHOICES}
          values={answers.received}
          noneValue="none"
          onChange={(values) => {
            updateAnswers({ received: values });
          }}
        />
      );
    case 12:
      return <ParentsStep />;
    case 13:
      return <ConfirmStep />;
    default:
      return <></>;
  }
}
