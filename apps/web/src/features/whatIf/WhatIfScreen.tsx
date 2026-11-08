import { useState } from 'react';
import type { JSX } from 'react';
import { navigate, paths } from '../../app/router.ts';
import type { ChangeSimulationRequest, ChangeSimulationResult, SimulationScenario } from '../../shared/api/simulations.ts';
import { ChoiceChip, ChoiceGroup } from '../../shared/ui/ChoiceChip.tsx';
import { SiteFooter } from '../../shared/ui/SiteFooter.tsx';
import { TopBar } from '../../shared/ui/TopBar.tsx';
import { useAnalysis } from '../benefits/analysisStore.ts';
import { formatManWon, shortPolicyId } from '../benefits/format.ts';
import { usePolicyInfo } from '../benefits/policyInfo.ts';
import { devSampleBenefit } from '../policy/devSampleBenefits.ts';
import { clearMyData } from '../profile/clearMyData.ts';
import { useFlowState } from '../profile/flowStore.ts';
import { toRecommendationRequest } from '../profile/toRequest.ts';
import { manText, netChange, periodShort, rateBarWidth, rateSentence, SCENARIO_CHIPS, signedMan } from './whatIfModel.ts';
import { useSimulation } from './useSimulation.ts';
import './WhatIfScreen.css';

/** S3에서 왔으면 S3으로, 아니면 이전 화면으로 */
function goBack(hasResult: boolean): void {
  if (hasResult) {
    navigate(paths.result);
    return;
  }
  if (window.history.length > 1) {
    window.history.back();
    return;
  }
  navigate(paths.start);
}

/** S4 상황이 바뀌면? (기획서 4.6). 주소 /what-if */
export function WhatIfScreen(): JSX.Element {
  const { answers } = useFlowState();
  const analysis = useAnalysis();
  const [scenario, setScenario] = useState<SimulationScenario>('employed');

  const hasConditions = answers.age !== null;
  // '독립하면'인데 이미 혼자 살면 계산할 것이 없다 (기획서 4.6)
  const alreadyAlone = scenario === 'independent' && answers.living === 'alone';
  const request: ChangeSimulationRequest | null =
    hasConditions && !alreadyAlone
      ? {
          domain: 'youth',
          profile: toRecommendationRequest(answers).profile,
          scenario,
          options: { period: '1y', present_value: false },
        }
      : null;
  const { state, retry } = useSimulation(request);

  return (
    <div className="page-width screen">
      <TopBar
        title="상황이 바뀌면?"
        onBack={() => {
          goBack(analysis.status === 'done');
        }}
      />
      <main className="screen__body s4-body">
        <h1 className="screen__title">지금 조건에서 무엇이 바뀌나요?</h1>

        {!hasConditions ? (
          <section className="ui-card s4-card">
            <h2 className="s4-card__title">내 조건을 먼저 골라 주세요</h2>
            <p className="s4-muted">조건을 고르면 상황이 바뀔 때 지원금이 어떻게 달라지는지 계산해 드려요.</p>
            <a className="ui-button ui-button--primary" href={paths.conditions(1)}>
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
                    setScenario(chip.value);
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
              <SimulationResult result={state.result} isSample={state.isSample} />
            ) : null}
          </>
        )}

        <p className="s4-note">
          계산은 바뀐 조건으로 조합을 다시 계산한 결과예요. 안내는 공고 근거와 계산 결과로 정리했어요. 세금·4대
          보험은 빼고 지원금 변화만 계산했어요.
        </p>
      </main>
      <SiteFooter onClearData={clearMyData} />
    </div>
  );
}

interface SimulationResultProps {
  result: ChangeSimulationResult;
  isSample: boolean;
}

function SimulationResult({ result, isSample }: SimulationResultProps): JSX.Element {
  const lost = result.lost ?? [];
  const gained = result.gained ?? [];
  const info = usePolicyInfo([...lost, ...gained].map((item) => item.policy_id));
  const diff = result.after_total_won - result.before_total_won;
  const net = netChange(result);
  const rate = result.effective_marginal_rate;
  const barWidth = rateBarWidth(rate);

  function name(id: string): string {
    return info.get(id)?.title ?? devSampleBenefit(id)?.title ?? `정책 ${shortPolicyId(id)}`;
  }

  return (
    <>
      {isSample ? (
        <p className="s4-dev-note">개발용 화면: 상황 변화 API가 아직 없어 가상 예시를 보여 줘요</p>
      ) : null}

      <div className="s4-grid">
        {/* 2 총액 비교 */}
        <section className="ui-card s4-card">
          <dl className="s4-rows">
            <dt>현재 추천 조합 ({periodShort(result.basis_period)})</dt>
            <dd>{formatManWon(result.before_total_won)}</dd>
            <dt>바뀐 뒤 추천 조합</dt>
            <dd>
              {formatManWon(result.after_total_won)}{' '}
              {diff !== 0 ? (
                <span className={diff < 0 ? 's4-down' : 's4-up'}>({signedMan(diff)})</span>
              ) : (
                <span className="s4-muted">(변화 없음)</span>
              )}
            </dd>
          </dl>
        </section>

        {/* 3 · 4 실제로 늘어나는 돈 + 실효한계세율 (소득이 바뀌는 상황에서만) */}
        {net !== null ? (
          <section className="ui-card s4-card">
            <h2 className="s4-card__title">실제로 늘어나는 돈</h2>
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
        ) : null}

        {/* 5 끊기는 정책 */}
        {lost.map((item) => (
          <section key={`lost-${item.policy_id}`} className="ui-card s4-card">
            <span className="s4-badge s4-badge--lose">✕ 끊기는 정책</span>
            <a className="s4-policy" href={paths.policy(item.policy_id)}>
              {name(item.policy_id)} ›
            </a>
            <p className="s4-muted">
              {item.reason}
              {item.amount_won !== null && item.amount_won !== undefined ? ` · ${formatManWon(item.amount_won)}` : ''}
            </p>
          </section>
        ))}

        {/* 새로 받는 정책 */}
        {gained.map((item) => (
          <section key={`gain-${item.policy_id}`} className="ui-card s4-card">
            <span className="s4-badge s4-badge--add">＋ 새로 받는 정책</span>
            <a className="s4-policy" href={paths.policy(item.policy_id)}>
              {name(item.policy_id)} ›
            </a>
            <p className="s4-muted">
              {item.reason}
              {item.amount_won !== null && item.amount_won !== undefined ? ` · ${formatManWon(item.amount_won)}` : ''}
            </p>
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
            <p>
              <span className="s4-ai">AI</span>
              {result.todo.text}
            </p>
            <p className="s4-small">
              근거: 공고 원문{' '}
              {result.todo.evidence.map((item) => `'${item.location ?? item.quote}'`).join(' · ')} 조항
            </p>
          </section>
        ) : null}
      </div>
    </>
  );
}
