import { useState } from 'react';
import type { JSX } from 'react';
import { CONDITION_STEPS, navigate, paths } from '../../app/router.ts';
import type { ChangeSimulationRequest, ChangeSimulationResult, SimulationScenario } from '../../shared/api/simulations.ts';
import { ChoiceChip, ChoiceGroup } from '../../shared/ui/ChoiceChip.tsx';
import { SiteFooter } from '../../shared/ui/SiteFooter.tsx';
import { TopBar } from '../../shared/ui/TopBar.tsx';
import { useAnalysis } from '../benefits/analysisStore.ts';
import { AmountBreakdown } from '../benefits/AmountBreakdown.tsx';
import { formatManWon, shortPolicyId } from '../benefits/format.ts';
import { usePolicyInfo } from '../benefits/policyInfo.ts';
import { readResultBasis, withResultBasis } from '../benefits/resultBasis.ts';
import { devSampleBenefit } from '../policy/devSampleBenefits.ts';
import { missingSteps } from '../profile/answerRules.ts';
import { clearMyData } from '../profile/clearMyData.ts';
import { useFlowState } from '../profile/flowStore.ts';
import { toRecommendationRequest } from '../profile/toRequest.ts';
import { manText, netChange, periodShort, rateBarWidth, rateSentence, SCENARIO_CHIPS, signedMan } from './whatIfModel.ts';
import { useSimulation } from './useSimulation.ts';
import './WhatIfScreen.css';

/** S4 상황이 바뀌면? (기획서 4.6). 주소 /what-if */
export function WhatIfScreen(): JSX.Element {
  const { answers, consent } = useFlowState();
  const analysis = useAnalysis();
  const [scenario, setScenario] = useState<SimulationScenario>('employed');
  const basis = readResultBasis(window.location.search);

  const missing = missingSteps(answers);
  const hasConditions = consent && missing.length === 0 && analysis.status === 'done';
  // '독립하면'인데 이미 혼자 살면 계산할 것이 없다 (기획서 4.6)
  const alreadyAlone = scenario === 'independent' && answers.living === 'alone';
  const request: ChangeSimulationRequest | null =
    hasConditions && !alreadyAlone
      ? {
          domain: 'youth',
          profile: toRecommendationRequest(answers).profile,
          scenario,
          options: basis,
        }
      : null;
  const { state, retry } = useSimulation(request);

  return (
    <div className="page-width screen">
      <TopBar
        title="상황이 바뀌면?"
        onBack={() => {
          navigate(analysis.status === 'done' ? withResultBasis(paths.result, basis) : paths.start);
        }}
      />
      <main className="screen__body s4-body">
        <h1 className="screen__title">지금 조건에서 무엇이 바뀌나요?</h1>

        {!hasConditions ? (
          <section className="ui-card s4-card">
            <h2 className="s4-card__title">내 조건을 먼저 골라 주세요</h2>
            <p className="s4-muted">조건을 고르면 상황이 바뀔 때 지원금이 어떻게 달라지는지 계산해 드려요.</p>
            <a className="ui-button ui-button--primary" href={consent ? paths.conditions(missing[0] ?? CONDITION_STEPS) : paths.start}>
              조건 고르기
            </a>
          </section>
        ) : (
          <>
            <ChoiceGroup label="바뀌는 상황">
              {SCENARIO_CHIPS.map((chip) => (
                <ChoiceChip
                  key={chip.value}
                  selected={scenario === chip.value}
                  onSelect={() => {
                    if (chip.value !== scenario) {
                      setScenario(chip.value);
                      retry();
                    }
                  }}
                >
                  {chip.label}
                </ChoiceChip>
              ))}
            </ChoiceGroup>

            {alreadyAlone ? (
              <section className="ui-card s4-card">
                <p>이미 혼자 살고 있어서 바뀌는 게 없어요.</p>
              </section>
            ) : null}
            {!alreadyAlone && state.status === 'loading' ? (
              <section className="ui-card s4-card" aria-live="polite">
                <p className="s4-muted">바뀐 조건으로 다시 계산하고 있어요</p>
              </section>
            ) : null}
            {!alreadyAlone && state.status === 'limit' ? (
              <section className="ui-card s4-card">
                <h2 className="s4-card__title">오늘 계산할 수 있는 횟수를 모두 썼어요</h2>
                <p className="s4-muted">내일 다시 시도해 주세요.</p>
              </section>
            ) : null}
            {!alreadyAlone && state.status === 'error' ? (
              <section className="ui-card s4-card">
                <h2 className="s4-card__title">계산하지 못했어요</h2>
                <p className="s4-muted">잠시 후 다시 시도해 주세요. 고른 조건은 그대로 남아 있어요.</p>
                <button type="button" className="ui-button ui-button--primary" onClick={retry}>
                  다시 시도
                </button>
              </section>
            ) : null}
            {!alreadyAlone && state.status === 'ok' ? (
              <SimulationResult result={state.result} isSample={state.isSample} presentValue={basis.present_value} />
            ) : null}
          </>
        )}

      </main>
      <SiteFooter onClearData={clearMyData} />
    </div>
  );
}

interface SimulationResultProps {
  result: ChangeSimulationResult;
  isSample: boolean;
  presentValue: boolean;
}

export function SimulationResult({ result, isSample, presentValue }: SimulationResultProps): JSX.Element {
  const lost = result.lost ?? [];
  const gained = result.gained ?? [];
  const info = usePolicyInfo(isSample ? [] : [...lost, ...gained].map((item) => item.policy_id));
  const diff = result.after_total_won - result.before_total_won;
  const net = netChange(result);
  const rate = result.effective_marginal_rate;
  const barWidth = rateBarWidth(rate);
  const detailBasis = { period: result.basis_period, present_value: !isSample && presentValue };

  function name(id: string): string {
    return (isSample ? devSampleBenefit(id)?.title : info.get(id)?.title) ?? `정책 ${shortPolicyId(id)}`;
  }

  return (
    <>
      {isSample ? (
        <p className="s4-dev-note">개발용 가상 예시: 입력 조건과 무관한 1년 기준이며 현재가치를 적용하지 않았어요</p>
      ) : result.virtual_data === true ? (
        <p className="s4-dev-note">입력 조건을 바탕으로 가상 정책을 다시 계산했어요. 실제 받을 수 있는 지원금이 아니에요.</p>
      ) : null}

      {!isSample && presentValue ? (
        <p className="s4-note">현재가치 합계와 순위에는 만기 전 예상 적립액을 넣지 않았어요.</p>
      ) : null}

      <div className="s4-grid">
        {/* 2 총액 비교 */}
        <section className="ui-card s4-card">
          <dl className="s4-rows">
            <dt>현재 추천 조합 ({periodShort(result.basis_period)}{!isSample && presentValue ? ' · 현재가치' : ''})</dt>
            <dd>
              {formatManWon(result.before_total_won)}
              {result.before_amount_breakdown !== undefined ? (
                <AmountBreakdown value={result.before_amount_breakdown} variant="compact" align="end" />
              ) : null}
            </dd>
            <dt>바뀐 뒤 추천 조합</dt>
            <dd>
              {formatManWon(result.after_total_won)}{' '}
              {diff !== 0 ? (
                <span className={diff < 0 ? 's4-down' : 's4-up'}>({signedMan(diff)})</span>
              ) : (
                <span className="s4-muted">(변화 없음)</span>
              )}
              {result.after_amount_breakdown !== undefined ? (
                <AmountBreakdown value={result.after_amount_breakdown} variant="compact" align="end" />
              ) : null}
            </dd>
            {result.annual.before_amount_breakdown !== undefined || result.annual.after_amount_breakdown !== undefined ? (
              <>
                <dt>현재 연간 지원금</dt>
                <dd>
                  {formatManWon(result.annual.before_won)}
                  {result.annual.before_amount_breakdown !== undefined ? (
                    <AmountBreakdown value={result.annual.before_amount_breakdown} variant="compact" align="end" />
                  ) : null}
                </dd>
                <dt>바뀐 뒤 연간 지원금</dt>
                <dd>
                  {formatManWon(result.annual.after_won)}
                  {result.annual.after_amount_breakdown !== undefined ? (
                    <AmountBreakdown value={result.annual.after_amount_breakdown} variant="compact" align="end" />
                  ) : null}
                </dd>
              </>
            ) : null}
          </dl>
        </section>

        {/* 3 · 4 실제로 늘어나는 돈 + 실효한계세율 (소득이 바뀌는 상황에서만) */}
        {net !== null ? (
          <section className="ui-card s4-card">
            <h2 className="s4-card__title">실제로 늘어나는 돈 (1년 기준)</h2>
            <dl className="s4-rows">
              <dt>연 소득</dt>
              <dd>
                {manText(net.incomeFrom)} → {manText(net.incomeTo)} 원{' '}
                <span className={net.incomeDiff < 0 ? 's4-down' : 's4-up'}>({signedMan(net.incomeDiff)})</span>
              </dd>
              <dt>줄어드는 지원금</dt>
              <dd className={net.benefitLoss > 0 ? 's4-down' : undefined}>{signedMan(-net.benefitLoss)} 원</dd>
              <dt>실제 순증가</dt>
              <dd className={net.net < 0 ? 's4-down' : 's4-up'}>{signedMan(net.net)} 원</dd>
            </dl>
            {barWidth !== null && rate !== null && rate !== undefined ? (
              <div className="s4-rate">
                <div className="s4-rate__track" aria-hidden="true">
                  <div className="s4-rate__fill" style={{ width: `${String(barWidth)}%` }} />
                </div>
                <p>{rateSentence(rate)}</p>
                {rate >= 100 ? (
                  <p className="s4-down">
                    기준선 바로 위에서는 소득이 올라도 손에 쥐는 돈이 줄어요 (혜택 절벽)
                  </p>
                ) : null}
              </div>
            ) : null}
            {result.income_change?.assumption_note ? (
              <p className="s4-small">{result.income_change.assumption_note}</p>
            ) : null}
          </section>
        ) : result.scenario === 'employed' || result.scenario === 'income_up' ? (
          <p className="s4-note">연 소득 증가분을 확인할 수 없어 실제 순증가와 실효한계세율을 표시하지 않아요.</p>
        ) : null}

        {/* 5 끊기는 정책 */}
        {lost.map((item) => (
          <section key={`lost-${item.policy_id}`} className="ui-card s4-card">
            <span className="s4-badge s4-badge--lose">✕ 끊기는 정책</span>
            <a className="s4-policy" href={withResultBasis(paths.policy(item.policy_id), detailBasis)}>
              {name(item.policy_id)} ›
            </a>
            <p className="s4-muted">
              {item.reason}
              {item.amount_won !== null && item.amount_won !== undefined ? ` · ${formatManWon(item.amount_won)}` : ''}
            </p>
            {item.amount_breakdown !== undefined ? (
              <AmountBreakdown value={item.amount_breakdown} variant="compact" />
            ) : null}
          </section>
        ))}

        {/* 새로 받는 정책 */}
        {gained.map((item) => (
          <section key={`gain-${item.policy_id}`} className="ui-card s4-card">
            <span className="s4-badge s4-badge--add">＋ 새로 받는 정책</span>
            <a className="s4-policy" href={withResultBasis(paths.policy(item.policy_id), detailBasis)}>
              {name(item.policy_id)} ›
            </a>
            <p className="s4-muted">
              {item.reason}
              {item.amount_won !== null && item.amount_won !== undefined ? ` · ${formatManWon(item.amount_won)}` : ''}
            </p>
            {item.amount_breakdown !== undefined ? (
              <AmountBreakdown value={item.amount_breakdown} variant="compact" />
            ) : null}
          </section>
        ))}

        {lost.length === 0 && gained.length === 0 ? (
          <section className="ui-card s4-card">
            <p className="s4-muted">이 상황에서는 끊기거나 새로 받는 정책이 없어요.</p>
          </section>
        ) : null}

        {/* 6 기준선 주의 */}
        {result.next_threshold ? (
          <section className="ui-card s4-card">
            <span className="s4-badge s4-badge--warn">! 기준선 주의</span>
            <p>{result.next_threshold.text}</p>
          </section>
        ) : null}

        {/* 7 지금 할 일 (공고 원문 근거가 있을 때만) */}
        {result.todo ? (
          <section className="ui-card s4-card s4-card--sub">
            <h2 className="s4-card__title">지금 할 일</h2>
            <p>{result.todo.text}</p>
            <ul className="s4-small">
              {result.todo.evidence.map((item, index) => (
                <li key={`${item.source.url}-${String(index)}`}>
                  <blockquote>{item.quote}</blockquote>
                  <a href={item.source.url} target="_blank" rel="noopener noreferrer">{item.location ?? '원문 근거'} ↗</a>
                </li>
              ))}
            </ul>
          </section>
        ) : null}
      </div>
      <p className="s4-note">{result.calculation_note}</p>
    </>
  );
}
