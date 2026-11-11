import type { JSX } from 'react';
import { CONDITION_STEPS, navigate, paths } from '../../app/router.ts';
import { conditionLabel } from '../../domains/youth/conditionLabels.ts';
import type { RecommendedService } from '../../shared/api/generated/recommendation-response.ts';
import { SiteFooter } from '../../shared/ui/SiteFooter.tsx';
import { StatusChip } from '../../shared/ui/StatusChip.tsx';
import { TopBar } from '../../shared/ui/TopBar.tsx';
import { useAnalysis } from '../benefits/analysisStore.ts';
import { formatManWon, recruitTag } from '../benefits/format.ts';
import { readResultBasis, withResultBasis } from '../benefits/resultBasis.ts';
import { clearMyData } from '../profile/clearMyData.ts';
import { useFlowState } from '../profile/flowStore.ts';
import { ServiceValueBasis } from './ServiceValueBasis.tsx';
import { hasExpiredServices } from './serviceModel.ts';
import './ServicesScreen.css';

/** S6 돈 말고 받을 수 있는 도움 (기획서 4.7-1). 주소 /services */
export function ServicesScreen(): JSX.Element {
  const analysis = useAnalysis();
  const { consent } = useFlowState();
  const result = consent && analysis.status === 'done' ? analysis.result : null;
  const basis = readResultBasis(window.location.search);
  const entries = result?.services ?? [];
  const expiredServices = hasExpiredServices(entries);
  const total = result?.services_total_won ?? null;
  const virtualData = result?.virtual_data === true || (analysis.status === 'done' && analysis.isSample);

  return (
    <div className="page-width screen">
      <TopBar
        title="돈 말고 받을 수 있는 도움"
        onBack={() => {
          if (window.history.length > 1) window.history.back();
          else navigate(result !== null ? withResultBasis(paths.result, basis) : paths.start);
        }}
      />
      <main className="screen__body s6-body">
        {virtualData && entries.length > 0 && !expiredServices ? (
          <p className="s6-dev-note">가상 서비스 목록이에요. 실제로 받을 수 있는 서비스가 아니에요.</p>
        ) : null}

        {/* 1 제목 */}
        <div className="s6-head">
          <h1 className="screen__title">돈 말고 받을 수 있는 도움</h1>
          <p className="s6-muted">정장 대여, 상담처럼 돈으로 주지 않는 지원이에요. 얼마짜리인지 시중 가격으로 바꿔 봤어요.</p>
        </div>

        {result === null ? (
          <section className="ui-card s6-card">
            <h2 className="s6-card__title">내 조건을 먼저 골라 주세요</h2>
            <p className="s6-muted">조건을 고르면 받을 수 있는 서비스를 찾아 드려요.</p>
            <a className="ui-button ui-button--primary" href={consent ? paths.conditions(1) : paths.start}>
              조건 고르기
            </a>
          </section>
        ) : expiredServices ? (
          <section className="ui-card s6-card">
            <h2 className="s6-card__title">서비스 모집 일정을 다시 확인해 주세요</h2>
            <p className="s6-muted">지난 분석에 마감된 서비스가 있어요.</p>
            <a className="ui-button ui-button--primary" href={paths.conditions(CONDITION_STEPS)}>
              조건 확인하고 다시 분석하기
            </a>
          </section>
        ) : entries.length === 0 ? (
          <section className="ui-card s6-card">
            <p className="s6-muted">{result.services !== undefined
              ? '지금 조건으로 받을 수 있는 서비스가 없어요.'
              : '이 분석에서는 서비스 목록을 제공하지 않아요.'}</p>
          </section>
        ) : (
          <>
            {/* 2 합계 안내 */}
            <section className="s6-total">
              <span className="s6-total__label">돈으로 치면 모두</span>
              <strong className="s6-total__amount">{total === null
                ? '환산 합계 없음'
                : `약 ${formatManWon(total).replace(/^약 /, '')}`}</strong>
              <span className="s6-total__note">이 금액은 지원금 조합 총액에 더하지 않았어요.</span>
            </section>

            {/* 3 · 4 · 5 서비스 행 */}
            <ul className="s6-list">
              {entries.map((entry) => (
                <ServiceRow key={entry.policy_id} entry={entry}
                  detailHref={withResultBasis(paths.policy(entry.policy_id), basis)} virtualData={virtualData} />
              ))}
            </ul>
          </>
        )}

        {/* 6 안내문 (S6-NOTE + COM-AI) */}
        <p className="s6-note">
          환산 금액은 이해를 돕기 위한 참고값이에요. 실제로 돈을 주는 것은 아니에요.{' '}
          {virtualData ? '가상 예시의 자격과 금액은 실제 신청에 사용할 수 없어요.' :
            '검수한 정책과 입력 조건으로 계산한 참고 결과예요. 최종 자격과 최신 조건은 신청 기관에서 확인해 주세요.'}
        </p>
      </main>
      <SiteFooter onClearData={clearMyData} />
    </div>
  );
}

interface ServiceRowProps {
  entry: RecommendedService;
  detailHref: string;
  virtualData: boolean;
}

function ServiceRow({ entry, detailHref, virtualData }: ServiceRowProps): JSX.Element {
  const value = entry.service.estimated_value_won;
  const tag = recruitTag(entry.application);
  const housing = entry.categories.includes('housing');

  return (
    <li className="s6-row">
      <a className="s6-row__main" href={detailHref}>
        <span className="s6-row__icon" aria-hidden="true">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            {housing ? (
              <>
                <path d="M3 11l9-7 9 7" />
                <path d="M5 10v10h14V10" />
                <path d="M10 20v-5h4v5" />
              </>
            ) : (
              <>
                <rect x="3" y="7" width="18" height="13" rx="2" />
                <path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M3 13h18" />
              </>
            )}
          </svg>
        </span>
        <span className="s6-row__text">
          <span className="s6-row__name">
            {entry.title} <span aria-hidden="true">›</span>
          </span>
          <span className="s6-row__item">{entry.service.item}</span>
        </span>
        <span className="s6-row__value">
          {value !== null ? (
            <strong>약 {formatManWon(value).replace(/^약 /, '')}</strong>
          ) : (
            <span className="s6-muted">환산 기준 없음</span>
          )}
          <span className={`s6-tag s6-tag--${tag.tone}`}>{tag.label}</span>
        </span>
      </a>

      {entry.eligibility.length > 0 ? (
        <span className="s6-row__chips">
          {entry.eligibility.map((condition) => (
            <StatusChip key={condition.condition_id} status={condition.status}>
              {conditionLabel(condition.condition_id)}
            </StatusChip>
          ))}
        </span>
      ) : null}

      {value !== null ? (
        <details className="s6-basis">
          <summary>환산 기준</summary>
          <p><ServiceValueBasis service={entry.service} virtualData={virtualData} /></p>
        </details>
      ) : (
        <p className="s6-small">시중 가격 근거를 찾지 못해 금액으로 바꾸지 않았어요. 합계에서도 뺐어요.</p>
      )}
    </li>
  );
}
