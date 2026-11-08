import type { JSX } from 'react';
import { navigate, paths } from '../../app/router.ts';
import { conditionLabel } from '../../domains/youth/conditionLabels.ts';
import type { NormalizedBenefit } from '../../shared/api/benefits.ts';
import { SiteFooter } from '../../shared/ui/SiteFooter.tsx';
import { StatusChip } from '../../shared/ui/StatusChip.tsx';
import { TopBar } from '../../shared/ui/TopBar.tsx';
import { useAnalysis } from '../benefits/analysisStore.ts';
import { isStale, monthDay, recruitTag, shortPolicyId, sourceName } from '../benefits/format.ts';
import { usePolicyInfo } from '../benefits/policyInfo.ts';
import { clearMyData } from '../profile/clearMyData.ts';
import { devSampleBenefit } from './devSampleBenefits.ts';
import {
  amountSummary,
  applyPeriod,
  conditionQuotes,
  diagnosisSummary,
  findMyPolicyResult,
} from './policyDetailModel.ts';
import type { MyPolicyResult } from './policyDetailModel.ts';
import { usePolicyDetail } from './usePolicyDetail.ts';
import './PolicyDetailScreen.css';

interface PolicyDetailScreenProps {
  policyId: string;
}

/** 이전 화면이 없으면(주소로 바로 들어오면) S1로 간다 */
function goBack(): void {
  if (window.history.length > 1) {
    window.history.back();
    return;
  }
  navigate(paths.start);
}

/** S5 정책 상세 (기획서 4.7). 주소 /policy/{정책ID} */
export function PolicyDetailScreen({ policyId }: PolicyDetailScreenProps): JSX.Element {
  const { state, retry } = usePolicyDetail(policyId);
  const analysis = useAnalysis();
  const mine = findMyPolicyResult(analysis.status === 'done' ? analysis.result : null, policyId);

  const title = state.status === 'ok' ? state.benefit.title : '정책 상세';

  return (
    <div className="page-width screen">
      <TopBar title={title} onBack={goBack} />
      {state.status === 'ok' ? (
        <PolicyDetail benefit={state.benefit} isSample={state.isSample} mine={mine} />
      ) : (
        <main className="screen__body s5-body">
          {state.status === 'loading' ? <p className="s5-muted">정책 정보를 불러오고 있어요</p> : null}
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
  mine: MyPolicyResult | null;
}

function PolicyDetail({ benefit, isSample, mine }: PolicyDetailProps): JSX.Element {
  const tag = recruitTag(benefit.application);
  const closed = benefit.application.status === 'closed';
  const applyUrl = benefit.application.url ?? benefit.source.url;
  const conflicts = benefit.conflicts ?? [];
  const conflictInfo = usePolicyInfo(conflicts.map((conflict) => conflict.policy_id));
  const reviewItems = mine?.eligibility.filter((item) => item.status === 'needs_review') ?? [];

  function conflictName(id: string): string {
    return conflictInfo.get(id)?.title ?? devSampleBenefit(id)?.title ?? `정책 ${shortPolicyId(id)}`;
  }

  return (
    <>
      <main className="screen__body s5-body">
        {isSample ? (
          <p className="s5-dev-note">개발용 화면: 정책 상세 API가 아직 없어 가상 예시를 보여 줘요</p>
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
            {mine !== null ? (
              <>
                <h2 className="s5-card__title">{diagnosisSummary(mine.eligibility)}</h2>
                <ul className="s5-conditions">
                  {mine.eligibility.map((condition) => (
                    <li key={condition.condition_id} className="s5-condition">
                      <StatusChip status={condition.status}>{conditionLabel(condition.condition_id)}</StatusChip>
                      {conditionQuotes(benefit, condition).map((item) => (
                        <blockquote key={`${item.location ?? ''}${item.quote}`} className="s5-quote">
                          {item.location !== null ? <span className="s5-quote__where">「{item.location}」</span> : null}
                          {item.quote}
                        </blockquote>
                      ))}
                    </li>
                  ))}
                </ul>
              </>
            ) : (
              <>
                <h2 className="s5-card__title">내 조건을 고르면 자격을 진단해 드려요</h2>
                <a className="ui-button ui-button--secondary" href={paths.conditions(1)}>
                  조건 고르기
                </a>
              </>
            )}
          </section>

          {/* 5 지원 내용 */}
          <section className="ui-card s5-card">
            <h2 className="s5-card__title">지원 내용</h2>
            <p className="s5-amount">{amountSummary(benefit.benefit_amount)}</p>
            {benefit.lifetime_once === true ? <p className="s5-muted">평생 한 번만 받을 수 있어요</p> : null}
          </section>

          {/* 6 함께 받을 수 없는 정책 (근거가 있을 때만) */}
          {conflicts.length > 0 ? (
            <section className="ui-card s5-card">
              <h2 className="s5-card__title">함께 받을 수 없는 정책</h2>
              <ul className="s5-conditions">
                {conflicts.map((conflict) => (
                  <li key={conflict.policy_id} className="s5-condition">
                    <a className="s5-link" href={paths.policy(conflict.policy_id)}>
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

          {/* 7 신청 방법 */}
          <section className="ui-card s5-card">
            <h2 className="s5-card__title">신청 방법</h2>
            <dl className="s5-rows">
              <dt>신청 기간</dt>
              <dd>{applyPeriod(benefit.application)}</dd>
              <dt>접수 기관</dt>
              <dd>{benefit.provider}</dd>
            </dl>
          </section>

          {/* 8 준비 서류 (정책 상세에 서류 정보가 아직 없어 문구 S5-DOC) */}
          <section className="ui-card s5-card">
            <h2 className="s5-card__title">준비 서류</h2>
            <p className="s5-muted">공식 자료에 서류 안내가 없어요. 신청 페이지에서 확인해 주세요.</p>
          </section>

          {/* 9 확인이 필요한 것 (내 조건으로 진단했을 때만) */}
          {mine !== null ? (
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
          {mine?.aiReason !== null && mine?.aiReason !== undefined ? (
            <section className="ui-card s5-card">
              <h2 className="s5-card__title">내 상황에서 볼 점</h2>
              <p>
                {mine.aiGenerated ? <span className="s5-ai">AI</span> : null}
                {mine.aiReason}
              </p>
            </section>
          ) : null}
        </div>

        {/* 10 출처 · 확인일 */}
        <p className="s5-source">
          출처 {sourceName(benefit.source.url)} · 마지막 확인 {monthDay(benefit.source.checked_at)} ·{' '}
          <a href={benefit.source.url} target="_blank" rel="noopener noreferrer">
            {benefit.source.url}
          </a>
          {isStale(benefit.source.checked_at) ? ' · ⚠ 최신 내용은 공식 페이지에서 확인' : ''}
        </p>
        <p className="s5-notice">
          검수한 정책과 입력 조건으로 계산한 참고 결과예요. 최종 자격과 최신 조건은 신청 기관에서 확인해 주세요.
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
        <a
          className="ui-button ui-button--primary ui-button--large ui-button--block"
          href={applyUrl}
          target="_blank"
          rel="noopener noreferrer"
        >
          {closed ? '공식 페이지 보기 ↗' : '신청하러 가기 ↗'}
        </a>
      </div>
    </>
  );
}
