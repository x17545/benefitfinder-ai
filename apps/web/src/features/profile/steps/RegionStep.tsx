import { useEffect } from 'react';
import type { JSX } from 'react';
import { Button } from '../../../shared/ui/Button.tsx';
import { ConditionStepLayout } from '../ConditionStepLayout.tsx';
import { updateAnswers, useFlowState } from '../flowStore.ts';
import { useRegionOptions } from '../regionOptions.ts';
import {
  isValidRegionSelection,
  normalizeRegionAnswers,
  regionLabel,
  resolveSidoSelection,
} from '../regionSelection.ts';

/** S2-2 region: sido -> sigungu. Sole-child sido auto-selects. */
export function RegionStep(): JSX.Element {
  const { sidoCode, regionCode, regionLabel: storedLabel } = useFlowState().answers;
  const { state, retry } = useRegionOptions();

  const regions = state.status === 'ready' ? state.regions : [];
  const selectedSido = regions.find((region) => region.sido_code === sidoCode);
  const selectionValid =
    state.status === 'ready' ? isValidRegionSelection(regions, sidoCode, regionCode) : false;

  useEffect(() => {
    if (state.status !== 'ready') {
      return;
    }
    const normalized = normalizeRegionAnswers(state.regions, sidoCode, regionCode);
    if (
      normalized.sidoCode !== sidoCode ||
      normalized.regionCode !== regionCode ||
      normalized.regionLabel !== storedLabel
    ) {
      updateAnswers(normalized);
    }
  }, [state, sidoCode, regionCode, storedLabel]);

  function selectSido(code: string): void {
    const resolved = resolveSidoSelection(regions, code);
    if (resolved === null) {
      return;
    }
    updateAnswers(resolved);
  }

  function selectSigungu(code: string): void {
    const sigungu = selectedSido?.sigungu.find((item) => item.code === code);
    if (selectedSido === undefined || sigungu === undefined) {
      return;
    }
    updateAnswers({ regionCode: sigungu.code, regionLabel: regionLabel(selectedSido.sido_name, sigungu.name) });
  }

  return (
    <ConditionStepLayout
      step={2}
      title="어디에 사나요?"
      description="주민등록상 주소 기준이에요"
      canNext={selectionValid}
    >
      {state.status === 'loading' ? <p className="s2-desc">지역 목록을 불러오고 있어요</p> : null}

      {state.status === 'error' ? (
        <div className="s2-sub">
          <p className="s2-desc">지역 목록을 불러오지 못했어요. 잠시 후 다시 시도해 주세요.</p>
          <div>
            <Button variant="secondary" onClick={retry}>
              다시 시도
            </Button>
          </div>
        </div>
      ) : null}

      {state.status === 'empty' ? (
        <div className="s2-sub">
          <p className="s2-desc">등록된 지역 목록이 비어 있어요. 잠시 후 다시 시도해 주세요.</p>
          <div>
            <Button variant="secondary" onClick={retry}>
              다시 시도
            </Button>
          </div>
        </div>
      ) : null}

      {state.status === 'ready' ? (
        <div className="s2-fields">
          <label className="s2-field">
            <span className="s2-field__label">시·도</span>
            <select
              className="s2-select"
              value={sidoCode ?? ''}
              onChange={(event) => {
                selectSido(event.target.value);
              }}
            >
              <option value="" disabled>
                선택해 주세요
              </option>
              {regions.map((region) => (
                <option key={region.sido_code} value={region.sido_code}>
                  {region.sido_name}
                </option>
              ))}
            </select>
          </label>

          <label className="s2-field">
            <span className="s2-field__label">시·군·구</span>
            <select
              className="s2-select"
              value={regionCode ?? ''}
              disabled={selectedSido === undefined}
              onChange={(event) => {
                selectSigungu(event.target.value);
              }}
            >
              <option value="" disabled>
                {selectedSido === undefined ? "시·도를 먼저 골라 주세요" : "선택해 주세요"}
              </option>
              {selectedSido?.sigungu.map((sigungu) => (
                <option key={sigungu.code} value={sigungu.code}>
                  {sigungu.name}
                </option>
              ))}
            </select>
          </label>
        </div>
      ) : null}
    </ConditionStepLayout>
  );
}
