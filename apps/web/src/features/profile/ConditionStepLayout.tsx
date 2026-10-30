import type { JSX, ReactNode } from 'react';
import { CONDITION_STEPS, navigate, paths } from '../../app/router.ts';
import { STEP_CATEGORIES } from '../../domains/youth/conditionOptions.ts';
import { Button } from '../../shared/ui/Button.tsx';
import { StepProgress } from '../../shared/ui/StepProgress.tsx';
import { TopBar } from '../../shared/ui/TopBar.tsx';
import './ConditionStep.css';

interface ConditionStepLayoutProps {
  /** 1 ~ 13 */
  step: number;
  /** 질문 (큰 글씨) */
  title: string;
  /** 질문 아래 설명 */
  description?: string;
  /** 필수 값을 다 골랐는지. false면 [다음]이 꺼진다 */
  canNext: boolean;
  /** 상단 바 오른쪽에 'n / 13' 앞에 붙는 것. 예: [건너뛰기] */
  topExtra?: ReactNode;
  nextLabel?: string;
  /** 기본은 다음 단계로 이동 */
  onNext?: () => void;
  children: ReactNode;
}

/** S2 공통 구성: 상단 바(뒤로, n / 13) + 진행 막대 + 질문 + 선택지 + 하단 [다음] (기획서 4.3) */
export function ConditionStepLayout({
  step,
  title,
  description,
  canNext,
  topExtra,
  nextLabel = '다음',
  onNext,
  children,
}: ConditionStepLayoutProps): JSX.Element {
  function goBack(): void {
    navigate(step > 1 ? paths.conditions(step - 1) : paths.start);
  }

  function goNext(): void {
    if (onNext !== undefined) {
      onNext();
      return;
    }
    navigate(step < CONDITION_STEPS ? paths.conditions(step + 1) : paths.loading);
  }

  return (
    <div className="page-width screen">
      <TopBar
        title="조건 고르기"
        onBack={goBack}
        right={
          <>
            {topExtra}
            <span>
              {step} / {CONDITION_STEPS}
            </span>
          </>
        }
      />
      <div className="s2-progress">
        <StepProgress current={step} total={CONDITION_STEPS} />
      </div>

      <main className="s2-body">
        <div className="s2-question">
          <p className="screen__eyebrow">{STEP_CATEGORIES[step]}</p>
          <h1 className="screen__title">{title}</h1>
          {description !== undefined ? <p className="s2-desc">{description}</p> : null}
        </div>
        {children}
      </main>

      <div className="s2-bottom">
        <Button size="large" block disabled={!canNext} onClick={goNext}>
          {nextLabel}
        </Button>
      </div>
    </div>
  );
}
