import type { JSX } from 'react';
import { Button } from '../../../shared/ui/Button.tsx';
import { ConditionStepLayout } from '../ConditionStepLayout.tsx';
import { updateAnswers, useFlowState } from '../flowStore.ts';
import { useRegionOptions } from '../regionOptions.ts';
import type { Region } from '../regionOptions.ts';

function regionLabel(region: Region, sigunguName: string): string {
  return region.sido_name === sigunguName ? sigunguName : `${region.sido_name} ${sigunguName}`;
}

/** S2-2 지역: 시·도 → 시·군·구. 하위가 하나면(세종) 자동 선택 */
export function RegionStep(): JSX.Element {
  const { sidoCode, regionCode } = useFlowState().answers;
  const { state, retry } = useRegionOptions();

  const regions = state.status === 'ready' ? state.regions : [];
  const selectedSido = regions.find((region) => region.sido_code === sidoCode);

  function selectSido(code: string): void {
    const region = regions.find((item) => item.sido_code === code);
    if (region === undefined) {
      return;
    }
    const only = region.sigungu.length === 1 ? region.sigungu[0] : undefined;
    updateAnswers({
      sidoCode: region.sido_code,
      regionCode: only !== undefined ? only.code : null,
      regionLabel: only !== undefined ? regionLabel(region, only.name) : null,
    });
  }

  function selectSigungu(code: string): void {
    const sigungu = selectedSido?.sigungu.find((item) => item.code === code);
    if (selectedSido === undefined || sigungu === undefined) {
      return;
    }
    updateAnswers({ regionCode: sigungu.code, regionLabel: regionLabel(selectedSido, sigungu.name) });
  }

  return (
    <ConditionStepLayout
      step={2}
      title="어디에 사나요?"
      description="주민등록상 주소 기준이에요"
      canNext={state.status === 'ready' && regionCode !== null}
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
                {selectedSido === undefined ? '시·도를 먼저 골라 주세요' : '선택해 주세요'}
              </option>
              {selectedSido?.sigungu.map((sigungu) => (
                <option key={sigungu.code} value={sigungu.code}>
                  {sigungu.name}
                </option>
              ))}
            </select>
          </label>

          {state.isSample ? (
            <p className="s2-dev-note">
              개발용 안내: 서버의 지역 목록(GET /api/v1/options)에 연결되지 않아 가상 목록을 보여 주고 있어요.
            </p>
          ) : null}
        </div>
      ) : null}
    </ConditionStepLayout>
  );
}
