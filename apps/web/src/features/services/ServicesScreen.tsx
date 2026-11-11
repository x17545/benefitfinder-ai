import type { JSX } from 'react';
import { navigate, paths } from '../../app/router.ts';
import { conditionLabel } from '../../domains/youth/conditionLabels.ts';
import type { NormalizedBenefit } from '../../shared/api/benefits.ts';
import { SiteFooter } from '../../shared/ui/SiteFooter.tsx';
import { StatusChip } from '../../shared/ui/StatusChip.tsx';
import { TopBar } from '../../shared/ui/TopBar.tsx';
import { useAnalysis } from '../benefits/analysisStore.ts';
import { formatManWon, recruitTag, shortPolicyId } from '../benefits/format.ts';
import { usePolicyInfo } from '../benefits/policyInfo.ts';
import { devSampleBenefit } from '../policy/devSampleBenefits.ts';
import { clearMyData } from '../profile/clearMyData.ts';
import { useFlowState } from '../profile/flowStore.ts';
import { readServices, serviceValueWon, servicesTotalWon, sortServices, valueBasisText } from './serviceModel.ts';
import type { ServiceEntry } from './serviceModel.ts';
import './ServicesScreen.css';

/** S6 돈 말고 받을 수 있는 도움 (기획서 4.7-1). 주소 /services */
export function ServicesScreen(): JSX.Element {
  const analysis = useAnalysis();
  const { consent } = useFlowState();
  const result = consent && analysis.status === 'done' ? analysis.result : null;
  const list = readServices(result);
  const info = usePolicyInfo(list.isSample ? [] : list.entries.map((entry) => entry.policy_id));

  function benefitOf(id: string): NormalizedBenefit | undefined {
    return (list.isSample ? devSampleBenefit(id) : info.get(id)) ?? undefined;
  }

  const entries = sortServices(list.entries, (id) => benefitOf(id)?.application.status === 'open');
  const total = servicesTotalWon(entries);

  return (
    <div className="page-width screen">
      <TopBar
        title="돈 말고 받을 수 있는 도움"
        onBack={() => {
          if (window.history.length > 1) window.history.back();
          else navigate(result !== null ? paths.result : paths.start);
        }}
      />
      <main className="screen__body s6-body">
        {list.isSample ? (
          <p className="s6-dev-note">개발용 화면: 추천 결과에 서비스 목록이 아직 없어 가상 예시를 보여 줘요</p>
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
        ) : entries.length === 0 ? (
          <section className="ui-card s6-card">
            <p className="s6-muted">지금 조건으로 받을 수 있는 서비스가 없어요.</p>
          </section>
        ) : (
          <>
            {/* 2 합계 안내 */}
            <section className="s6-total">
              <span className="s6-total__label">돈으로 치면 모두</span>
              <strong className="s6-total__amount">약 {formatManWon(total).replace(/^약 /, '')}</strong>
              <span className="s6-total__note">이 금액은 지원금 조합 총액에 더하지 않았어요.</span>
            </section>

            {/* 3 · 4 · 5 서비스 행 */}
            <ul className="s6-list">
              {entries.map((entry) => (
                <ServiceRow key={entry.policy_id} entry={entry} benefit={benefitOf(entry.policy_id)} />
              ))}
            </ul>
          </>
        )}

        {/* 6 안내문 (S6-NOTE + COM-AI) */}
        <p className="s6-note">
          환산 금액은 이해를 돕기 위한 참고값이에요. 실제로 돈을 주는 것은 아니에요. 검수한 정책과 입력 조건으로
          계산한 참고 결과예요. 최종 자격과 최신 조건은 신청 기관에서 확인해 주세요.
        </p>
      </main>
      <SiteFooter onClearData={clearMyData} />
    </div>
  );
}

interface ServiceRowProps {
  entry: ServiceEntry;
  benefit: NormalizedBenefit | undefined;
}

function ServiceRow({ entry, benefit }: ServiceRowProps): JSX.Element {
  const value = serviceValueWon(entry.service);
  const tag = benefit !== undefined ? recruitTag(benefit.application) : null;
  const housing = benefit?.categories.includes('housing') ?? false;
  const basis = valueBasisText(entry.service);

  return (
    <li className="s6-row">
      <a className="s6-row__main" href={paths.policy(entry.policy_id)}>
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
            {benefit?.title ?? `정책 ${shortPolicyId(entry.policy_id)}`} <span aria-hidden="true">›</span>
          </span>
          <span className="s6-row__item">{entry.service.item}</span>
        </span>
        <span className="s6-row__value">
          {value !== null ? (
            <strong>약 {formatManWon(value).replace(/^약 /, '')}</strong>
          ) : (
            <span className="s6-muted">환산 기준 없음</span>
          )}
          {tag !== null ? <span className={`s6-tag s6-tag--${tag.tone}`}>{tag.label}</span> : null}
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
          <p>{basis}</p>
        </details>
      ) : (
        <p className="s6-small">시중 가격 근거를 찾지 못해 금액으로 바꾸지 않았어요. 합계에서도 뺐어요.</p>
      )}
    </li>
  );
}
