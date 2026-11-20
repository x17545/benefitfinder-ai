import { useEffect, useRef, useState } from 'react';
import type { JSX } from 'react';
import { CONDITION_STEPS, lastNavigationKind, navigate, paths } from '../../app/router.ts';
import { trackEvent } from '../../shared/analytics/events.ts';
import { Button } from '../../shared/ui/Button.tsx';
import { SiteFooter } from '../../shared/ui/SiteFooter.tsx';
import { showToast } from '../../shared/ui/toastStore.ts';
import { TopBar } from '../../shared/ui/TopBar.tsx';
import { clearMyData } from '../profile/clearMyData.ts';
import { conditionSummary } from '../profile/conditionSummary.ts';
import { useFlowState } from '../profile/flowStore.ts';
import { useAnalysis } from './analysisStore.ts';
import { AmountBreakdown } from './AmountBreakdown.tsx';
import { FallbackList } from './FallbackList.tsx';
import { amountBreakdownText, formatManWon, josa, PERIOD_TABS, withJosa } from './format.ts';
import type { Period } from './format.ts';
import { PolicyCard } from './PolicyCard.tsx';
import { PortfolioCard } from './PortfolioCard.tsx';
import { resultImageAmountBasisNote, saveResultImage } from './resultImage.ts';
import { ResultLists } from './ResultLists.tsx';
import { readResultBasis, withResultBasis } from './resultBasis.ts';
import { foldElementId, rememberFold, rememberScroll, startResultView } from './resultViewMemory.ts';
import type { FoldId } from './resultViewMemory.ts';
import { hasExpiredServices } from '../services/serviceModel.ts';
import '../services/ServicesScreen.css';
import { eligibilityDisplayStatus, pickView, policyName, policySummaries, portfolioEligibilityStatus, portfolioLabel } from './resultModel.ts';
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

/** S3-E 결과 없음 (문구 S3-E1, S3-E2). 빈 기간 탭에서도 같은 안내를 쓴다. */
function EmptyResultContent(): JSX.Element {
  return (
    <>
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
    </>
  );
}

function EmptyResult(): JSX.Element {
  return (
    <div className="page-width screen">
      <ResultTopBar />
      <main className="s3-empty">
        <EmptyResultContent />
      </main>
      <SiteFooter onClearData={clearMyData} />
    </div>
  );
}

function hasResults(view: ResultView): boolean {
  return view.portfolios.length > 0 || (view.fallback_list?.length ?? 0) > 0;
}

/** 계산할 조합이 없어도 보완 목록과 계산 불가 사유는 결과 화면에 남긴다. */
function hasResultContent(view: ResultView): boolean {
  return hasResults(view) || view.calculation_status === 'unavailable' ||
    (view.not_yet_reviewed?.length ?? 0) > 0 || (view.closed_now?.length ?? 0) > 0 ||
    (view.not_eligible?.length ?? 0) > 0;
}

/** S3 내 지원금 조합 (기획서 4.5) */
export function ResultScreen(): JSX.Element {
  const analysis = useAnalysis();
  const { answers } = useFlowState();
  const [period, setPeriod] = useState<Period>(() => readResultBasis(window.location.search).period);
  const [presentValue, setPresentValue] = useState(() => readResultBasis(window.location.search).present_value);
  const [selectedRank, setSelectedRank] = useState(1);

  const result = analysis.status === 'done' ? analysis.result : null;
  // 상세에서 [뒤로]로 돌아온 경우에만 펼친 목록과 스크롤 위치를 되살린다
  const memoryKey = result?.request_id ?? '';
  const [restored] = useState(() => startResultView(memoryKey, lastNavigationKind() === 'pop'));
  const rootRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (restored.windowY <= 0 && restored.screenTop <= 0) {
      return;
    }
    // App이 화면 이동 때 맨 위로 올린 다음에 되살린다
    const timer = setTimeout(() => {
      if (restored.windowY > 0) {
        window.scrollTo(0, restored.windowY);
      }
      if (rootRef.current !== null && restored.screenTop > 0) {
        rootRef.current.scrollTop = restored.screenTop;
      }
    }, 0);
    return () => {
      clearTimeout(timer);
    };
  }, [restored]);

  /** 결과 화면 안의 링크(정책 상세 등)를 누르는 순간의 스크롤 위치를 기억한다 */
  function rememberPosition(): void {
    if (memoryKey !== '') {
      rememberScroll(memoryKey, window.scrollY, rootRef.current?.scrollTop ?? 0);
    }
  }

  const info = policySummaries(result);
  // F14: 돈 말고 받을 수 있는 서비스 수 (현금 총액에는 더하지 않는다)
  const expiredServices = hasExpiredServices(result?.services);
  const serviceCount = expiredServices ? 0 : result?.services?.length ?? 0;

  // 결과 없이 주소로 들어오면 처음으로
  useEffect(() => {
    if (analysis.status !== 'done') {
      navigate(paths.start, { replace: true });
    }
  }, [analysis.status]);

  if (result === null || analysis.status !== 'done') {
    return <></>;
  }

  // view는 초기 1년의 표시 형식이다. 다른 기간에 결과가 있으면 탭을 남긴다.
  const anyContent = PERIOD_TABS.some(({ value: key }) => {
    const item = result.results[key];
    return hasResultContent(item.pv_off) || hasResultContent(item.pv_on);
  });
  if (!anyContent && serviceCount === 0 && !expiredServices) {
    return <EmptyResult />;
  }

  const view = pickView(result, period, presentValue);
  const portfolios = view.portfolios;
  const selected = portfolios.find((item) => item.rank === selectedRank) ?? portfolios[0];
  const top = portfolios[0];
  const periodShort = PERIOD_TABS.find((tab) => tab.value === period)?.short ?? '';
  const summary = conditionSummary(answers);
  const rate = result.discount_rate !== null ? (result.discount_rate * 100).toFixed(1) : null;
  const hasAmountBreakdown = portfolios.some((portfolio) =>
    portfolio.amount_breakdown !== undefined ||
    portfolio.policies.some((policy) => policy.amount_breakdown !== undefined),
  );

  // 기간이나 현재가치를 바꾸면 추천 조합을 다시 선택한다 (기획서 4.5 3·4번)
  function changePeriod(next: Period): void {
    if (next !== period) {
      trackEvent({ name: 'period_change', period: next });
    }
    setPeriod(next);
    setSelectedRank(1);
    navigate(withResultBasis(paths.result, { period: next, present_value: presentValue }), { replace: true });
  }

  function togglePresentValue(next: boolean): void {
    if (next !== presentValue) {
      trackEvent({ name: 'pv_toggle', on: next });
    }
    setPresentValue(next);
    setSelectedRank(1);
    navigate(withResultBasis(paths.result, { period, present_value: next }), { replace: true });
  }

  const conflict = view.why?.deciding_conflict ?? [];
  const pvSuffix = presentValue ? ' (현재가치 기준)' : '';
  const heroLabel = top?.amount_breakdown !== undefined
    ? presentValue
      ? `${periodShort} 동안 지급 예정 현금의 현재가치`
      : `${periodShort} 동안 예상 지원금 합계`
    : `${periodShort} 동안 함께 받을 수 있는 최대 금액${pvSuffix}`;
  const fallback = view.fallback_list ?? [];
  const hasPendingReview = (view.not_yet_reviewed?.length ?? 0) > 0;
  const closedCount = view.closed_now?.length ?? 0;
  const notEligibleCount = view.not_eligible?.length ?? 0;
  const reviewCount = view.not_yet_reviewed?.length ?? 0;
  // 현금 지원은 없지만 볼 목록(검수 전·모집 끝남·조건 안 맞음)이 있는 경우
  const noCashWithLists = !hasResults(view) && (hasPendingReview || closedCount > 0 || notEligibleCount > 0);
  // 조합이 0개면 다음에 할 수 있는 일을 안내한다 (S3-E 전체 빈 화면은 따로 안내)
  const showNextSteps = portfolios.length === 0 && (fallback.length > 0 || noCashWithLists || serviceCount > 0);

  function toggleFold(id: FoldId, open: boolean): void {
    rememberFold(memoryKey, id, open);
  }

  function openFold(id: FoldId): void {
    const element = document.getElementById(foldElementId(id));
    if (element instanceof HTMLDetailsElement) {
      element.open = true;
      rememberFold(memoryKey, id, true);
      element.scrollIntoView({ behavior: 'smooth', block: 'start' });
      element.querySelector('summary')?.focus({ preventScroll: true });
    }
  }

  // F10: 조합 카드와 지금 고른 조합의 지원금만 그림으로 저장한다 (조건은 넣지 않는다)
  function saveImage(): void {
    if (selected === undefined) {
      return;
    }
    void saveResultImage({
      periodLabel: `${PERIOD_TABS.find((tab) => tab.value === period)?.label ?? ''}${presentValue ? ' · 현재가치' : ''}`,
      amountBasisNote: resultImageAmountBasisNote(presentValue, hasAmountBreakdown),
      combos: portfolios.map((portfolio) => ({
        label: portfolioLabel(portfolio.rank),
        total: formatManWon(portfolio.total_won),
        breakdown: portfolio.amount_breakdown !== undefined
          ? amountBreakdownText(portfolio.amount_breakdown)
          : undefined,
        names: portfolio.policies.map((policy) => policyName(policy.policy_id, info)).join(' + '),
        selected: portfolio.rank === selected.rank,
      })),
      listTitle: `${portfolioLabel(selected.rank)}에 들어 있는 지원금 ${String(selected.policies.length)}개`,
      policies: selected.policies.map((policy) => ({
        name: policyName(policy.policy_id, info),
        amount:
          policy.amount_in_period_won !== null && policy.amount_in_period_won !== undefined
            ? formatManWon(policy.amount_in_period_won)
            : '금액 확인 필요',
        breakdown: policy.amount_breakdown !== undefined
          ? amountBreakdownText(policy.amount_breakdown)
          : undefined,
      })),
      createdAt: new Date(),
      virtualData: analysis.status === 'done' && (analysis.isSample || result?.virtual_data === true),
    }).then((saved) => {
      if (saved) {
        trackEvent({ name: 'image_save' });
        showToast('결과 이미지를 저장했어요');
      }
    });
  }

  return (
    <div className="page-width screen s3" ref={rootRef} onClickCapture={rememberPosition}>
      <ResultTopBar />

      <main className="s3-body">
        {analysis.isSample || result.virtual_data === true ? (
          <p className="s2-dev-note">가상 정책으로 계산한 예제예요. 실제 받을 수 있는 지원금이 아니에요.</p>
        ) : null}

        <section className="s3-heading">
          {summary !== '' ? <p className="s3-summary">{summary}</p> : null}
          {hasResults(view) ? (
            <h1 className="screen__title">
              {portfolios.length > 0
                ? (portfolios.every((portfolio) => portfolioEligibilityStatus(portfolio.policies) === 'met')
                    ? `입력 조건상 충족한 예상 조합 ${String(portfolios.length)}개를 찾았어요`
                    : `자격 확인이 필요한 예상 조합 ${String(portfolios.length)}개를 찾았어요`)
                : fallback.some((item) => eligibilityDisplayStatus(item.eligibility) === 'not_met')
                  ? `신청 조건을 다시 확인해야 하는 지원금 ${String(fallback.length)}개를 찾았어요`
                  : fallback.every((item) => eligibilityDisplayStatus(item.eligibility) === 'met')
                    ? `입력 조건상 충족한 지원금 ${String(fallback.length)}개를 찾았어요`
                    : `자격 확인이 필요한 지원금 ${String(fallback.length)}개를 찾았어요`}
            </h1>
          ) : noCashWithLists ? (
            <>
              <h1 className="screen__title">지금 바로 받을 수 있는 지원금은 찾지 못했어요</h1>
              <p className="s2-desc">
                이 조건으로 금액을 계산할 수 있는 현금 지원이 아직 없어요. 아래 ‘다음에 해 볼 수 있는 것’을 확인해 주세요.
              </p>
            </>
          ) : serviceCount > 0 ? (
            <h1 className="screen__title">확인할 수 있는 서비스가 있어요</h1>
          ) : expiredServices ? (
            <h1 className="screen__title">서비스 모집 일정을 다시 확인해 주세요</h1>
          ) : null}
        </section>

        {top !== undefined ? (
          <section className="s3-hero" aria-label="추천 조합 요약">
            <p className="s3-hero__label">{heroLabel}</p>
            <p className="s3-hero__total">{formatManWon(top.total_won)}</p>
            {portfolioEligibilityStatus(top.policies) !== 'met' ? (
              <p className="s3-hero__note" role="note">
                자격 미확인 정책이 포함된 조건부 예상액이에요. 실제 수급 가능 여부를 확인해야 해요.
              </p>
            ) : null}
            {top.amount_breakdown !== undefined ? (
              <AmountBreakdown value={top.amount_breakdown} variant="hero" />
            ) : null}
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

        {anyContent ? <div className="s3-controls">
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
            <div className="s3-pv__copy">
              <label htmlFor="s3-pv">
                <b>현재가치로 보기</b>{' '}
                <span>
                  나중에 받는 돈을 지금 가치로 바꿔 비교해요{rate !== null ? ` (할인율 연 ${rate}%)` : ''}
                </span>
              </label>
              {presentValue ? (
                <p className="s3-pv__note">
                  현재가치 합계와 순위에는 만기 전 예상 적립액을 넣지 않았어요.
                </p>
              ) : null}
            </div>
          </div>
        </div> : null}

        {view.calculation_status === 'unavailable' ? (
          <p className="s3-note s3-note--check">
            ? 이 기준으로는 계산할 수 없어요. 지급 시점이나 기준금리를 확인하지 못했어요.
          </p>
        ) : null}

        {serviceCount === 0 && !expiredServices && view.calculation_status === 'available' &&
          !hasResults(view) && !hasPendingReview ? (
          <section className="s3-empty"><EmptyResultContent /></section>
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
                    if (portfolio.rank !== selected?.rank) {
                      trackEvent({ name: 'combo_select', rank: portfolio.rank });
                    }
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
                <PolicyCard key={policy.policy_id} policy={policy} info={info} aiGenerated={result.ai_generated}
                  rank={selected.rank}
                  virtualData={analysis.isSample || result.virtual_data === true}
                  detailHref={withResultBasis(paths.policy(policy.policy_id), { period, present_value: presentValue })} />
              ))}
            </div>
          </section>
        ) : null}

        {portfolios.length === 0 && fallback.length > 0 ? (
          <section className="s3-section">
            <p className="s2-desc">함께 묶을 수 있는 정책이 없어 개별 예상 지원금을 보여 드려요. 실제 신청 자격과 지급 여부는 공식 기준을 확인해야 해요.</p>
            <FallbackList items={fallback} info={info}
              virtualData={analysis.isSample || result.virtual_data === true} />
          </section>
        ) : null}

        {expiredServices ? (
          <section className="s3-note s3-note--check">
            <p>지난 분석에 마감된 서비스가 있어요.</p>
            <a href={paths.conditions(CONDITION_STEPS)}>조건 확인하고 다시 분석하기</a>
          </section>
        ) : serviceCount > 0 ? (
          <a className="s6-entry" href={withResultBasis(paths.services, { period, present_value: presentValue })}>
            <span>
              돈 말고 받을 수 있는 도움 {serviceCount}개
              <span className="s6-entry__note"> · 현금 총액과 따로 봐요</span>
            </span>
            <span aria-hidden="true">›</span>
          </a>
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

        {showNextSteps ? (
          <NextSteps
            closedCount={closedCount}
            reviewCount={reviewCount}
            notEligibleCount={notEligibleCount}
            onOpenFold={openFold}
          />
        ) : null}

        <ResultLists view={view} selected={selected} info={info} aiGenerated={result.ai_generated}
          openFolds={restored.open} onToggleFold={toggleFold} />

        <p className="s3-disclaimer">
          {view.not_yet_reviewed?.some((item) => item.verification_only === true)
            ? '사람이 승인한 일부 원문 근거를 확인하는 검증 화면이에요. 자격·금액·조합은 계산하지 않았어요.'
            : '검수한 정책과 입력 조건으로 계산한 참고 결과예요. 최종 자격과 최신 조건은 신청 기관에서 확인해 주세요.'}
        </p>
      </main>

      <SiteFooter onClearData={clearMyData} />

      {anyContent ? <div className="s3-bottom">
        <div className="s3-bottom__inner">
          {selected !== undefined ? (
            <Button size="large" variant="secondary" onClick={saveImage}>
              결과 이미지로 저장
            </Button>
          ) : null}
          <Button
            size="large"
            onClick={() => {
              navigate(withResultBasis(paths.whatIf, { period, present_value: presentValue }));
            }}
          >
            상황이 바뀌면 어떻게 될까?
          </Button>
        </div>
      </div> : null}
    </div>
  );
}

interface NextStepsProps {
  closedCount: number;
  reviewCount: number;
  notEligibleCount: number;
  onOpenFold: (id: FoldId) => void;
}

/** 추천 조합이 0개일 때 '다음에 해 볼 수 있는 것' (서비스 목록 진입은 바로 위 카드가 맡는다) */
function NextSteps({ closedCount, reviewCount, notEligibleCount, onOpenFold }: NextStepsProps): JSX.Element {
  return (
    <section className="s3-next" aria-labelledby="s3-next-title">
      <h2 className="s3-section__title" id="s3-next-title">다음에 해 볼 수 있는 것</h2>
      <ul className="s3-next__list">
        {closedCount > 0 ? (
          <li>
            <button type="button" className="s3-next__item" onClick={() => { onOpenFold('closed'); }}>
              <b>모집이 끝난 정책 {closedCount}개 확인하기</b>
              <span>다음 모집 때 신청할 수 있는 정책이에요</span>
            </button>
          </li>
        ) : null}
        {reviewCount > 0 ? (
          <li>
            <button type="button" className="s3-next__item" onClick={() => { onOpenFold('notReviewed'); }}>
              <b>검수 중인 정책 {reviewCount}개 살펴보기</b>
              <span>금액은 아직 계산 전이에요. 정책마다 전체 자격 진단만 볼 수 있어요</span>
            </button>
          </li>
        ) : null}
        {notEligibleCount > 0 ? (
          <li>
            <button type="button" className="s3-next__item" onClick={() => { onOpenFold('notEligible'); }}>
              <b>조건이 맞지 않은 정책 {notEligibleCount}개 이유 보기</b>
              <span>어떤 조건 때문에 빠졌는지 알 수 있어요</span>
            </button>
          </li>
        ) : null}
        <li>
          <a className="s3-next__item" href={paths.conditions(CONDITION_STEPS)}>
            <b>조건 바꿔서 다시 찾기</b>
            <span>관심 분야나 지금 상황을 바꾸면 다른 정책이 나올 수 있어요</span>
          </a>
        </li>
        <li>
          <a className="s3-next__item" href="https://www.youthcenter.go.kr" target="_blank" rel="noopener noreferrer">
            <b>온통청년에서 더 찾아보기 ↗</b>
            <span>전국 청년 정책을 직접 검색할 수 있어요</span>
          </a>
        </li>
      </ul>
    </section>
  );
}

/** 문구 S3-CK: '{A}'과 '{B}'은 함께 받을 수 있는지 공식 자료로 확인되지 않았어요. */
function pairSentence(a: string, b: string): string {
  return `'${a}'${josa(a, '과', '와')} '${b}'${josa(b, '은', '는')} 함께 받을 수 있는지 공식 자료로 확인되지 않았어요.`;
}
