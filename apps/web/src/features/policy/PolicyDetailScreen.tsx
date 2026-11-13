import type { JSX } from 'react';
import { CONDITION_STEPS, navigate, paths } from '../../app/router.ts';
import { conditionLabel } from '../../domains/youth/conditionLabels.ts';
import type { NormalizedBenefit } from '../../shared/api/benefits.ts';
import { SiteFooter } from '../../shared/ui/SiteFooter.tsx';
import { StatusChip } from '../../shared/ui/StatusChip.tsx';
import { TopBar } from '../../shared/ui/TopBar.tsx';
import { useAnalysis } from '../benefits/analysisStore.ts';
import { formatManWon, isStale, monthDay, recruitTag, shortPolicyId, sourceName } from '../benefits/format.ts';
import { usePolicyInfo } from '../benefits/policyInfo.ts';
import { PolicyExplanation } from '../benefits/PolicyExplanation.tsx';
import { readResultBasis, withResultBasis } from '../benefits/resultBasis.ts';
import type { PortfolioPolicy } from '../benefits/resultModel.ts';
import { clearMyData } from '../profile/clearMyData.ts';
import { useFlowState } from '../profile/flowStore.ts';
import { findServiceForDetail } from '../services/serviceModel.ts';
import type { RecommendedService } from '../../shared/api/generated/recommendation-response.ts';
import { ServiceValueBasis } from '../services/ServiceValueBasis.tsx';
import '../services/ServicesScreen.css';
import { devSampleBenefit } from './devSampleBenefits.ts';
import {
  amountSummary,
  applyPeriod,
  diagnosisSummary,
  findMyPolicyResult,
  payoutTimingSummary,
} from './policyDetailModel.ts';
import { usePolicyDetail } from './usePolicyDetail.ts';
import { ApplicationGuide } from './ApplicationGuide.tsx';
import './PolicyDetailScreen.css';

interface PolicyDetailScreenProps {
  policyId: string;
}

/** S5 정책 상세 (기획서 4.7). 주소 /policy/{정책ID} */
export function PolicyDetailScreen({ policyId }: PolicyDetailScreenProps): JSX.Element {
  const { state, retry } = usePolicyDetail(policyId);
  const analysis = useAnalysis();
  const { consent } = useFlowState();
  const basis = readResultBasis(window.location.search);
  const result = consent && analysis.status === 'done' ? analysis.result : null;
  const mine = state.status === 'ok' && state.benefit.service === undefined && result !== null && analysis.status === 'done'
    ? findMyPolicyResult(result, state.benefit, basis, state.isSample, analysis.isSample) : null;
  // 서비스형 정책(F14)은 조합에 들어가지 않으므로 S6 목록의 진단을 쓴다
  const serviceDiagnosis = state.status === 'ok'
    ? findServiceForDetail(result, state.benefit, analysis.status === 'done' && analysis.isSample)
    : null;
  const conditionsHref = consent ? paths.conditions(result !== null ? CONDITION_STEPS : 1) : paths.start;

  const title = state.status === 'ok' ? state.benefit.title : '정책 상세';

  return (
    <div className="page-width screen">
      <TopBar title={title} onBack={() => {
        if (window.history.length > 1) window.history.back();
        else navigate(result !== null ? withResultBasis(paths.result, basis) : paths.start);
      }} />
      {state.status === 'ok' ? (
        <PolicyDetail benefit={state.benefit} isSample={state.isSample} mine={mine}
          serviceDiagnosis={serviceDiagnosis?.eligibility ?? null}
          hasAnalysis={result !== null} conditionsHref={conditionsHref} aiGenerated={result?.ai_generated ?? false} />
      ) : (
        <main className="screen__body s5-body">
          {state.status === 'loading' ? <p className="s5-muted" role="status">정책 정보를 불러오고 있어요</p> : null}
          {state.status === 'notFound' ? (
            <section className="s5-message">
              <h1 className="screen__title">찾는 정책이 없어요</h1>
              <p className="s5-muted">마감됐거나 주소가 바뀌었을 수 있어요</p>
              <a className="ui-button ui-button--primary" href={paths.start}>
                처음으로
              </a>
            </section>
          ) : null}
          {state.status === 'error' ? (
            <section className="s5-message">
              <h1 className="screen__title">정책 정보를 불러오지 못했어요</h1>
              <p className="s5-muted">잠시 후 다시 시도해 주세요</p>
              <div className="s5-message__actions">
                <button type="button" className="ui-button ui-button--primary" onClick={retry}>
                  다시 시도
                </button>
                <a className="ui-button ui-button--secondary" href={paths.start}>
                  처음으로
                </a>
              </div>
            </section>
          ) : null}
        </main>
      )}
      <SiteFooter onClearData={clearMyData} />
    </div>
  );
}

interface PolicyDetailProps {
  benefit: NormalizedBenefit;
  isSample: boolean;
  mine: PortfolioPolicy | null;
  serviceDiagnosis: RecommendedService['eligibility'] | null;
  hasAnalysis: boolean;
  conditionsHref: string;
  aiGenerated: boolean;
}

function PolicyDetail({ benefit, isSample, mine, serviceDiagnosis, hasAnalysis, conditionsHref, aiGenerated }: PolicyDetailProps): JSX.Element {
  const service = benefit.service ?? null;
  const virtualData = isSample || benefit.virtual_data === true;
  const tag = recruitTag(benefit.application);
  const closed = benefit.application.status === 'closed';
  const applicationUrl = benefit.application.status === 'open' ? benefit.application.url : null;
  const applyUrl = applicationUrl ?? benefit.source.url;
  const conflicts = benefit.conflicts ?? [];
  const conflictInfo = usePolicyInfo(isSample ? [] : conflicts.map((conflict) => conflict.policy_id));
  const diagnosis = service !== null ? serviceDiagnosis : mine?.eligibility ?? null;
  const reviewItems = diagnosis?.filter((item) => item.status === 'needs_review') ?? [];
  const serviceValue = service?.estimated_value_won ?? null;
  const payoutTiming = payoutTimingSummary(benefit.benefit_amount);

  function conflictName(id: string): string {
    return (isSample ? devSampleBenefit(id)?.title : conflictInfo.get(id)?.title) ?? `정책 ${shortPolicyId(id)}`;
  }

  return (
    <>
      <main className="screen__body s5-body">
        {isSample ? (
          <p className="s5-dev-note">개발용 화면: 정책 상세 API가 아직 없어 가상 예시를 보여 줘요</p>
        ) : virtualData ? (
          <p className="s5-dev-note">{service !== null
            ? '가상 서비스의 상세예요. 실제 모집이나 지원을 뜻하지 않아요.'
            : '가상 정책의 상세예요. 실제 모집이나 지원금을 뜻하지 않아요.'}</p>
        ) : null}

        {/* 1 · 2 기관 · 모집 상태 · 정책 이름 */}
        <header className="s5-head">
          <div className="s5-head__meta">
            <span>{benefit.provider}</span>
            <span className={`s5-tag s5-tag--${tag.tone}`}>{tag.label}</span>
          </div>
          <h1 className="screen__title">{benefit.title}</h1>
          <p className="s5-muted">{benefit.summary}</p>
        </header>

        <div className="s5-grid">
          {/* 3 · 4 내 자격 진단 + 원문 인용 */}
          <section className="ui-card s5-card">
            {diagnosis !== null ? (
              <>
                <h2 className="s5-card__title">{diagnosisSummary(diagnosis)}</h2>
                <ul className="s5-conditions">
                  {diagnosis.map((condition) => (
                    <li key={condition.condition_id} className="s5-condition">
                      <StatusChip status={condition.status}>{conditionLabel(condition.condition_id)}</StatusChip>
                      {condition.evidence.map((item, index) => (
                        <blockquote key={`${item.source.url}-${String(index)}`} className="s5-quote">
                          {item.location !== undefined ? <span className="s5-quote__where">「{item.location}」</span> : null}
                          {item.quote}
                        </blockquote>
                      ))}
                    </li>
                  ))}
                </ul>
              </>
            ) : (
              <>
                <h2 className="s5-card__title">{hasAnalysis ? '이 정책의 현재 진단을 확인할 수 없어요' : '내 조건을 고르면 자격을 진단해 드려요'}</h2>
                <a className="ui-button ui-button--secondary" href={conditionsHref}>
                  {hasAnalysis ? '조건 확인하고 다시 분석하기' : '조건 고르기'}
                </a>
              </>
            )}
          </section>

          {benefit.virtual_data === true && benefit.eligibility_conditions.length > 0 &&
            (service === null || diagnosis === null) ? (
            <section className="ui-card s5-card">
              <h2 className="s5-card__title">가상 정책의 공개 기준</h2>
              {benefit.eligibility_conditions.flatMap((condition) => condition.evidence.map((item, index) => (
                <blockquote key={`${condition.id}-${String(index)}`} className="s5-quote">{item.quote}</blockquote>
              )))}
            </section>
          ) : null}

          {/* 5 지원 내용. 서비스형(F14)이면 받을 수 있는 것 + 돈으로 치면 */}
          {service !== null ? (
            <>
              <section className="ui-card s5-card">
                <h2 className="s5-card__title">받을 수 있는 것</h2>
                <p className="s5-amount">{service.item}</p>
              </section>
              <section className="ui-card s5-card">
                <h2 className="s5-card__title">돈으로 치면</h2>
                {serviceValue !== null ? (
                  <>
                    <p className="s5-amount">약 {formatManWon(serviceValue).replace(/^약 /, '')}</p>
                    <p className="s6-value-basis"><ServiceValueBasis service={service} virtualData={virtualData} /></p>
                  </>
                ) : (
                  <p className="s5-muted">시중 가격 근거를 찾지 못해 금액으로 바꾸지 않았어요.</p>
                )}
                <p className="s5-muted">지원금 조합 총액에는 더하지 않았어요.</p>
              </section>
            </>
          ) : (
            <section className="ui-card s5-card">
              <h2 className="s5-card__title">지원 내용</h2>
              <p className="s5-amount">{amountSummary(benefit.benefit_amount)}</p>
              {payoutTiming !== null ? <p className="s5-timing">{payoutTiming}</p> : null}
              {benefit.lifetime_once === true ? <p className="s5-muted">평생 한 번만 받을 수 있어요</p> : null}
            </section>
          )}

          {/* 6 함께 받을 수 없는 정책 (근거가 있을 때만) */}
          {conflicts.length > 0 ? (
            <section className="ui-card s5-card">
              <h2 className="s5-card__title">함께 받을 수 없는 정책</h2>
              <ul className="s5-conditions">
                {conflicts.map((conflict) => (
                  <li key={conflict.policy_id} className="s5-condition">
                    <a className="s5-link" href={withResultBasis(paths.policy(conflict.policy_id), readResultBasis(window.location.search))}>
                      {conflictName(conflict.policy_id)} ›
                    </a>
                    {conflict.evidence.map((item) => (
                      <blockquote key={`${item.location ?? ''}${item.quote}`} className="s5-quote">
                        {item.location !== undefined ? <span className="s5-quote__where">「{item.location}」</span> : null}
                        {item.quote}
                      </blockquote>
                    ))}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          <ApplicationGuide benefit={benefit} virtualData={virtualData} />

          {/* 9 확인이 필요한 것 (내 조건으로 진단했을 때만) */}
          {diagnosis !== null ? (
            <section className="ui-card s5-card s5-card--sub">
              <h2 className="s5-card__title">확인이 필요한 것</h2>
              {reviewItems.length > 0 ? (
                <ul className="s5-review">
                  {reviewItems.map((item) => (
                    <li key={item.condition_id}>
                      <b>? {conditionLabel(item.condition_id)}</b>:{' '}
                      {item.reason !== '' ? item.reason : '입력하지 않았거나 공식 기준이 불명확해요'}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="s5-muted">없어요</p>
              )}
            </section>
          ) : null}

          {/* 9-1 맞춤 설명 (내 조건으로 진단했고 설명이 있을 때만) */}
          {service === null && mine !== null && (mine.ai_reason?.trim() || (mine.ai_sources?.length ?? 0) > 0) ? (
            <section className="ui-card s5-card">
              <h2 className="s5-card__title">내 상황에서 볼 점</h2>
              <PolicyExplanation policy={mine} aiGenerated={aiGenerated} />
            </section>
          ) : null}
        </div>

        {/* 10 출처 · 확인일 */}
        <p className="s5-source">
          출처 {sourceName(benefit.source.url)} · 마지막 확인 {monthDay(benefit.source.checked_at)} ·{' '}
          {virtualData ? benefit.source.url : <a href={benefit.source.url} target="_blank" rel="noopener noreferrer">
            {benefit.source.url}
          </a>}
          {!virtualData && isStale(benefit.source.checked_at) ? ' · ⚠ 최신 내용은 공식 페이지에서 확인' : ''}
        </p>
        <p className="s5-notice">
          정책 안내는 참고용이에요. 최종 자격과 최신 조건은 신청 기관에서 확인해 주세요.
        </p>
      </main>

      {/* 11 하단 고정 버튼 */}
      <div className="s5-bottom">
        {closed ? (
          <p className="s5-closed">
            지금은 신청 기간이 아니에요. 다음 모집 때 신청할 수 있어요
            {benefit.application.starts_on !== null || benefit.application.ends_on !== null
              ? ` (지난 모집 ${applyPeriod(benefit.application)})`
              : ''}
          </p>
        ) : null}
        {virtualData ? <p className="s5-muted">가상 예시에는 실제 신청 링크가 없어요.</p> : <a
          className="ui-button ui-button--primary ui-button--large ui-button--block"
          href={applyUrl}
          target="_blank"
          rel="noopener noreferrer"
        >
          {applicationUrl !== null ? '신청하러 가기 ↗' : '공식 페이지 보기 ↗'}
        </a>}
      </div>
    </>
  );
}
