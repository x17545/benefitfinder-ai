import type { JSX } from 'react';
import { navigate, paths } from '../../app/router.ts';
import { Button } from '../../shared/ui/Button.tsx';
import { conditionSummary } from './conditionSummary.ts';
import { updateAnswers, updateFlowState } from './flowStore.ts';
import { useRegionOptions } from './regionOptions.ts';
import type { SavedConditions } from './savedConditions.ts';

interface SavedConditionsCardProps {
  saved: SavedConditions;
  onClear: () => void;
}

/** S1 지난 조건 카드 (문구 S1-SAVED). 저장된 조건이 있을 때만 보인다 */
export function SavedConditionsCard({ saved, onClear }: SavedConditionsCardProps): JSX.Element {
  const { state } = useRegionOptions();

  // 저장값에는 지역 코드만 있어서, 이름은 지역 목록에서 찾는다
  let regionLabel: string | null = null;
  if (state.status === 'ready') {
    const sido = state.regions.find((region) => region.sido_code === saved.answers.sidoCode);
    const sigungu = sido?.sigungu.find((item) => item.code === saved.answers.regionCode);
    if (sido !== undefined && sigungu !== undefined) {
      regionLabel = sido.sido_name === sigungu.name ? sigungu.name : `${sido.sido_name} ${sigungu.name}`;
    }
  }
  const answers = { ...saved.answers, regionLabel };

  function startAgain(): void {
    // 저장할 때 이미 동의했으므로 동의와 기억하기를 켠 상태로 다시 분석한다
    updateFlowState({ consent: true, remember: true });
    updateAnswers(answers);
    navigate(paths.loading);
  }

  return (
    <section className="s1-saved ui-card" aria-label="지난 조건">
      <h2 className="s1-saved__title">지난번 조건으로 바로 보기</h2>
      <p className="s1-saved__summary">{conditionSummary(answers)}</p>
      <div className="s1-saved__actions">
        <Button onClick={startAgain}>이 조건으로 다시 보기</Button>
        <Button variant="secondary" onClick={onClear}>
          내 정보 지우기
        </Button>
      </div>
    </section>
  );
}
