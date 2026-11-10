import type { JSX } from 'react';
import { navigate, paths } from '../../app/router.ts';
import { Button } from '../../shared/ui/Button.tsx';
import { conditionSummary } from './conditionSummary.ts';
import { updateAnswers, updateFlowState } from './flowStore.ts';
import { useRegionOptions } from './regionOptions.ts';
import type { SavedConditions } from './savedConditions.ts';
import { isValidRegionSelection, normalizeRegionAnswers } from './regionSelection.ts';

interface SavedConditionsCardProps {
  saved: SavedConditions;
  onClear: () => void;
}

/** S1 지난 조건 카드 (문구 S1-SAVED). 저장된 조건이 있을 때만 보인다 */
export function SavedConditionsCard({ saved, onClear }: SavedConditionsCardProps): JSX.Element {
  const { state, retry } = useRegionOptions();

  // 저장값에는 지역 코드만 있어서, 이름은 지역 목록에서 찾는다
  const normalized = state.status === 'ready'
    ? normalizeRegionAnswers(state.regions, saved.answers.sidoCode, saved.answers.regionCode)
    : { sidoCode: saved.answers.sidoCode, regionCode: null, regionLabel: null };
  const answers = { ...saved.answers, ...normalized };
  const regionOk = state.status === 'ready' &&
    isValidRegionSelection(state.regions, answers.sidoCode, answers.regionCode);

  function startAgain(): void {
    if (state.status !== 'ready') { return; }
    // 저장할 때 이미 동의했으므로 동의와 기억하기를 켠 상태로 다시 분석한다
    updateFlowState({ consent: true, remember: true });
    updateAnswers(answers);
    navigate(regionOk ? paths.loading : paths.conditions(2));
  }

  return (
    <section className="s1-saved ui-card" aria-label="지난 조건">
      <h2 className="s1-saved__title">지난번 조건으로 바로 보기</h2>
      <p className="s1-saved__summary">{conditionSummary(answers)}</p>
      <div className="s1-saved__actions">
        <Button disabled={state.status !== 'ready'} onClick={startAgain}>
          {state.status === 'ready' && !regionOk ? '지역 다시 선택하기' : '이 조건으로 다시 보기'}
        </Button>
        <Button variant="secondary" onClick={onClear}>
          내 정보 지우기
        </Button>
      </div>
      {state.status === 'loading' ? <p>지역 목록을 확인하고 있어요</p> : null}
      {state.status === 'empty' || state.status === 'error' ? (
        <div role="status">
          <p>{state.status === 'empty' ? '등록된 지역 목록이 비어 있어요.' : '지역 목록을 불러오지 못했어요.'}</p>
          <Button variant="secondary" onClick={retry}>다시 시도</Button>
        </div>
      ) : null}
    </section>
  );
}
