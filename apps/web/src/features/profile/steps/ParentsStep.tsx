import type { JSX } from 'react';
import { navigate, paths } from '../../../app/router.ts';
import {
  HOUSEHOLD_SIZE_CHOICES,
  PARENT_ASSET_CHOICES,
  PARENT_INCOME_CHOICES,
} from '../../../domains/youth/conditionOptions.ts';
import { TERM_HELP } from '../../../domains/youth/termHelp.ts';
import { trackEvent } from '../../../shared/analytics/events.ts';
import { ConditionStepLayout } from '../ConditionStepLayout.tsx';
import { updateAnswers, useFlowState } from '../flowStore.ts';
import { ChipQuestion } from './ChipQuestion.tsx';
import { PrivacyNote } from './PrivacyNote.tsx';

const PARENT_AGE_NOTE_FROM = 30;

/** S2-12 부모님 정보 (선택). 기혼이면 질문 대신 안내만 */
export function ParentsStep(): JSX.Element {
  const { age, marital, householdSize, parentIncome, parentAsset } = useFlowState().answers;
  const married = marital === 'married';

  function skip(): void {
    trackEvent({ name: 'step_skip' });
    updateAnswers({ parentsSkipped: true, householdSize: null, parentIncome: null, parentAsset: null });
    navigate(paths.conditions(13));
  }

  if (married) {
    return (
      <ConditionStepLayout
        step={12}
        title="더 정확하게 볼까요?"
        description="부모님 정보 입력은 건너뛰어요. 정책별 부모님 기준은 따로 확인해요."
        canNext
      >
        {null}
      </ConditionStepLayout>
    );
  }

  // 사용자 테스트: 부모님 소득·재산을 몰라 답하기 어렵거나 민감하다는 의견이 가장 많았다.
  // 선택 질문이고, 모르거나 답하기 싫으면 넘어가도 결과를 볼 수 있다는 점을 먼저 알린다.
  const description =
    "선택 질문이에요. 일부 정책만 부모님(원가구) 소득·재산을 봐요. 모르면 '모름'을 고르거나 건너뛰어도 결과를 볼 수 있고, 그 조건은 '확인 필요'로 표시돼요" +
    (age !== null && age >= PARENT_AGE_NOTE_FROM ? '. 만 30세 이상이면 안 보는 정책도 있어요' : '');

  return (
    <ConditionStepLayout
      step={12}
      title="더 정확하게 볼까요?"
      description={description}
      canNext
      topExtra={
        <button type="button" className="s2-skip" onClick={skip}>
          건너뛰기
        </button>
      }
    >
      <ChipQuestion
        title="가구원 수"
        help={TERM_HELP.householdSize}
        choices={HOUSEHOLD_SIZE_CHOICES}
        value={householdSize}
        onChange={(value) => {
          updateAnswers({ householdSize: value, parentsSkipped: false });
        }}
      />
      <ChipQuestion
        title="부모님 집 한 달 소득"
        description="세전, 가구 전체"
        help={TERM_HELP.parentIncome}
        choices={PARENT_INCOME_CHOICES}
        value={parentIncome}
        onChange={(value) => {
          updateAnswers({ parentIncome: value, parentsSkipped: false });
        }}
      />
      <ChipQuestion
        title="부모님 재산"
        help={TERM_HELP.parentAsset}
        choices={PARENT_ASSET_CHOICES}
        value={parentAsset}
        onChange={(value) => {
          updateAnswers({ parentAsset: value, parentsSkipped: false });
        }}
      />
      <div className="s2-skip-box">
        <p>답하기 어렵거나 원하지 않으면 건너뛰어도 괜찮아요.</p>
        <button type="button" className="ui-button ui-button--secondary" onClick={skip}>
          부모님 정보 없이 다음으로
        </button>
      </div>
      <PrivacyNote>부모님 정보는 자격 계산에만 써요. 서버에서는 계산이 끝나면 바로 폐기하고, 저장하거나 AI로 보내지 않아요.</PrivacyNote>
    </ConditionStepLayout>
  );
}
