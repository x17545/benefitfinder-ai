import { useEffect, useState } from 'react';
import type { JSX } from 'react';
import { CONDITION_STEPS, navigate, paths } from '../../app/router.ts';
import { Button } from '../../shared/ui/Button.tsx';
import { SiteFooter } from '../../shared/ui/SiteFooter.tsx';
import { TopBar } from '../../shared/ui/TopBar.tsx';
import { clearMyData } from '../profile/clearMyData.ts';
import { conditionSummary } from '../profile/conditionSummary.ts';
import { useFlowState } from '../profile/flowStore.ts';
import { useAnalysis } from './analysisStore.ts';
import { formatManWon, josa, PERIOD_TABS, withJosa } from './format.ts';
import type { Period } from './format.ts';
import { PolicyCard } from './PolicyCard.tsx';
import { PortfolioCard } from './PortfolioCard.tsx';
import { usePolicyInfo } from './policyInfo.ts';
import { allPolicyIds, pickView, policyName, portfolioLabel } from './resultModel.ts';
import type { ResultView } from './resultModel.ts';
import './ResultScreen.css';

const BAR_COLORS = ['var(--color-accent)', 'var(--color-bar-3)', 'var(--color-hero-muted)'];

function editConditions(): void {
  navigate(paths.conditions(CONDITION_STEPS));
}

function ResultTopBar(): JSX.Element {
  return (
    <TopBar
      title="내 지원금 조합"
      onBack={editConditions}
      right={
        <a className="s3-top-link" href={paths.conditions(CONDITION_STEPS)}>
          조건 수정
        </a>
      }
    />
  );
}

/** S3-E 결과 없음 (문구 S3-E1, S3-E2) */
function EmptyResult(): JSX.Element {
  return (
    <div className="page-width screen">
      <ResultTopBar />
      <main className="s3-empty">
        <h1 className="screen__title">지금 다루는 정책 중엔 맞는 게 없어요</h1>
        <p className="s2-desc">첫 MVP는 취업·주거 분야를 다뤄요 (생활·자산형성·교육장학은 확장 예정)</p>
        <div className="s3-empty__actions">
          <Button size="large" block onClick={editConditions}>
            조건 바꾸기
          </Button>
          <a className="ui-button ui-button--secondary ui-button--large ui-button--block" href="https://www.youthcenter.go.kr" target="_blank" rel="noopener noreferrer">
            온통청년에서 더 찾아보기 ↗
          </a>
          <a className="ui-button ui-button--secondary ui-button--large ui-button--block" href="https://www.bokjiro.go.kr" target="_blank" rel="noopener noreferrer">
            복지로에서 더 찾아보기 ↗
          </a>
        </div>
      </main>
      <SiteFooter onClearData={clearMyData} />
    </div>
  );
}

function hasResults(view: ResultView): boolean {
  return view.portfolios.length > 0 || (view.fallback_list?.length ?? 0) > 0;
}

/** S3 내 지원금 조합 (기획서 4.5) */
export function ResultScreen(): JSX.Element {
  const analysis = useAnalysis();
  const { answers } = useFlowState();
  const [period, setPeriod] = useState<Period>('1y');
  const [presentValue, setPresentValue] = useState(false);
  const [selectedRank, setSelectedRank] = useState(1);

  const result = analysis.status === 'done' ? analysis.result : null;
  const info = usePolicyInfo(result !== null ? allPolicyIds(result) : []);

  // 결과 없이 주소로 들어오면 처음으로
  useEffect(() => {
    if (analysis.status !== 'done') {
      navigate(paths.start, { replace: true });
    }
  }, [analysis.status]);

  if (result === null || analysis.status !== 'done') {
    return <></>;
  }

  const firstView = pickView(result, '1y', false);
  if (result.view === 'empty' || !hasResults(firstView)) {
    return <EmptyResult />;
  }

  const view = pickView(result, period, presentValue);
  const portfolios = view.portfolios;
  const selected = portfolios.find((item) => item.rank === selectedRank) ?? portfolios[0];
  const top = portfolios[0];
  const periodShort = PERIOD_TABS.find((tab) => tab.value === period)?.short ?? '';
  const summary = conditionSummary(answers);
  const rate = result.discount_rate !== null ? (result.discount_rate * 100).toFixed(1) : null;

  // 기간이나 현재가치를 바꾸면 추천 조합을 다시 선택한다 (기획서 4.5 3·4번)
  function changePeriod(next: Period): void {
    setPeriod(next);
    setSelectedRank(1);
  }

  function togglePresentValue(next: boolean): void {
    setPresentValue(next);
    setSelectedRank(1);
  }

  const conflict = view.why?.deciding_conflict ?? [];
  const pvSuffix = presentValue ? ' (현재가치 기준)' : '';

  return (
    <div className="page-width screen s3">
      <ResultTopBar />

      <main className="s3-body">
        {analysis.isSample ? (
          <p className="s2-dev-note">개발용 예제 결과(contracts/fixtures)예요. 실제 결과가 아니에요.</p>
        ) : null}

        <section className="s3-heading">
          {summary !== '' ? <p className="s3-summary">{summary}</p> : null}
          <h1 className="screen__title">함께 받을 수 있는 조합 {portfolios.length}개를 찾았어요</h1>
        </section>

        {top !== undefined ? (
          <section className="s3-hero" aria-label="추천 조합 요약">
            <p className="s3-hero__label">{periodShort} 동안 함께 받을 수 있는 최대 금액{pvSuffix}</p>
            <p className="s3-hero__total">{formatManWon(top.total_won)}</p>
            <p className="s3-hero__names">
              추천 조합 · {top.policies.map((policy) => policyName(policy.policy_id, info)).join(' + ')}
            </p>
            <span className="s3-bar s3-bar--hero" aria-hidden="true">
              {top.policies.map((policy, index) => (
                <span
                  key={policy.policy_id}
                  style={{ flexGrow: policy.amount_in_period_won ?? 0, background: BAR_COLORS[index % BAR_COLORS.length] }}
                />
              ))}
            </span>
            <span className="s3-hero__legend">
              {top.policies.map((policy, index) => (
                <span key={policy.policy_id}>
                  <i style={{ background: BAR_COLORS[index % BAR_COLORS.length] }} aria-hidden="true" />
                  {policyName(policy.policy_id, info)}{' '}
                  {policy.amount_in_period_won !== null && policy.amount_in_period_won !== undefined
                    ? formatManWon(policy.amount_in_period_won)
                    : ''}
                </span>
              ))}
            </span>
          </section>
        ) : null}

        <div className="s3-controls">
          <div className="s3-tabs" role="tablist" aria-label="기간">
            {PERIOD_TABS.map((tab) => (
              <button
                key={tab.value}
                type="button"
                role="tab"
                aria-selected={period === tab.value}
                className="s3-tab"
                onClick={() => {
                  changePeriod(tab.value);
                }}
              >
                {tab.label}
              </button>
            ))}
          </div>
          <div className="s3-pv">
            <input
              id="s3-pv"
              type="checkbox"
              checked={presentValue}
              onChange={(event) => {
                togglePresentValue(event.target.checked);
              }}
            />
            <label htmlFor="s3-pv">
              <b>현재가치로 보기</b>{' '}
              <span>
                나중에 받는 돈을 지금 가치로 바꿔 비교해요{rate !== null ? ` (할인율 연 ${rate}%)` : ''}
              </span>
            </label>
          </div>
        </div>

        {view.calculation_status === 'unavailable' ? (
          <p className="s3-note s3-note--check">
            ? 이 기준으로는 계산할 수 없어요. 지급 시점이나 기준금리를 확인하지 못했어요.
          </p>
        ) : null}

        {portfolios.length > 0 ? (
          <section className="s3-section">
            <h2 className="s3-section__title">조합 비교</h2>
            <div className="s3-combos">
              {portfolios.map((portfolio) => (
                <PortfolioCard
                  key={portfolio.rank}
                  portfolio={portfolio}
                  topTotal={top?.total_won ?? portfolio.total_won}
                  selected={portfolio.rank === selected?.rank}
                  onSelect={() => {
                    setSelectedRank(portfolio.rank);
                  }}
                  info={info}
                />
              ))}
            </div>
          </section>
        ) : null}

        {portfolios.length >= 2 && view.why !== null && view.why !== undefined ? (
          <section className="s3-why">
            <h2 className="s3-section__title">왜 이 순서인가요?</h2>
            {view.why.gap_won !== null && view.why.gap_won !== undefined ? (
              <p>
                추천 조합은 대안 1보다 {periodShort} 동안 <b>{formatManWon(view.why.gap_won)}</b> 더 받아요.{pvSuffix}
              </p>
            ) : null}
            {view.why.second_total_won !== null && view.why.second_total_won !== undefined ? (
              <p>
                추천 조합을 고르는 기회비용은 다음으로 좋은 대안 1의 {formatManWon(view.why.second_total_won)}이에요.
                그보다 커서 1위예요.
              </p>
            ) : null}
            {conflict.length === 2 ? (
              <p>
                {withJosa(policyName(conflict[0], info), '과', '와')} {withJosa(policyName(conflict[1], info), '은', '는')}{' '}
                함께 받을 수 없어요. 그래서 둘 중 총액이 커지는 쪽을 골랐어요.
              </p>
            ) : null}
            {period !== '1y' ? <p>기간을 바꾸면 매달·만기 지급 금액이 달라져 순위가 바뀔 수 있어요.</p> : null}
          </section>
        ) : null}

        {selected !== undefined ? (
          <section className="s3-section">
            <h2 className="s3-section__title">
              {portfolioLabel(selected.rank)}에 들어 있는 지원금 {selected.policies.length}개
            </h2>
            <div className="s3-policies">
              {selected.policies.map((policy) => (
                <PolicyCard key={policy.policy_id} policy={policy} info={info} aiGenerated={result.ai_generated} />
              ))}
            </div>
          </section>
        ) : null}

        {selected?.unverified_pairs !== undefined && selected.unverified_pairs.length > 0 ? (
          <section className="s3-note s3-note--check">
            <h2 className="s3-note__title">? 이 조합에서 확인이 필요한 부분</h2>
            {selected.unverified_pairs.map((pair) => {
              const amount = selected.policies.find((policy) => policy.policy_id === pair.b)?.amount_in_period_won;
              return (
                <p key={`${pair.a}-${pair.b}`}>
                  {pairSentence(policyName(pair.a, info), policyName(pair.b, info))}
                  {amount !== null && amount !== undefined
                    ? ` (${formatManWon(amount)}이 총액에 포함돼 있어요)`
                    : ''}
                </p>
              );
            })}
          </section>
        ) : null}

        <p className="s3-disclaimer">
          검수한 정책과 입력 조건으로 계산한 참고 결과예요. 최종 자격과 최신 조건은 신청 기관에서 확인해 주세요.
        </p>
      </main>

      <SiteFooter onClearData={clearMyData} />

      <div className="s3-bottom">
        <div className="s3-bottom__inner">
          <Button
            size="large"
            onClick={() => {
              navigate(paths.whatIf);
            }}
          >
            상황이 바뀌면 어떻게 될까?
          </Button>
        </div>
      </div>
    </div>
  );
}

/** 문구 S3-CK: '{A}'과 '{B}'은 함께 받을 수 있는지 공식 자료로 확인되지 않았어요. */
function pairSentence(a: string, b: string): string {
  return `'${a}'${josa(a, '과', '와')} '${b}'${josa(b, '은', '는')} 함께 받을 수 있는지 공식 자료로 확인되지 않았어요.`;
}
