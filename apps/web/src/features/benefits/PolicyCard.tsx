import type { JSX } from 'react';
import { paths } from '../../app/router.ts';
import { conditionLabel } from '../../domains/youth/conditionLabels.ts';
import type { NormalizedBenefit } from '../../shared/api/benefits.ts';
import { StatusChip } from '../../shared/ui/StatusChip.tsx';
import { formatManWon, isStale, monthDay, recruitTag, sourceName } from './format.ts';
import { policyName } from './resultModel.ts';
import type { PortfolioPolicy } from './resultModel.ts';

interface PolicyCardProps {
  policy: PortfolioPolicy;
  info: ReadonlyMap<string, NormalizedBenefit>;
  /** 결과 설명을 생성형 AI가 썼을 때만 AI 표시 */
  aiGenerated: boolean;
}

function PolicyIcon({ categories }: { categories: readonly string[] }): JSX.Element {
  const housing = categories.includes('housing');
  return (
    <span className="s3-policy__icon" aria-hidden="true">
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
  );
}

/** 지원금 행 (S3 9 ~ 12번) */
export function PolicyCard({ policy, info, aiGenerated }: PolicyCardProps): JSX.Element {
  const benefit = info.get(policy.policy_id);
  const tag = benefit !== undefined ? recruitTag(benefit.application) : null;
  const amount = policy.amount_in_period_won;
  const original = policy.original_amount_won;
  const showOriginal =
    amount !== null && amount !== undefined && original !== null && original !== undefined && original !== amount;
  const applyUrl = benefit?.application.url ?? policy.source.url;

  return (
    <article className="s3-policy">
      <a className="s3-policy__main" href={paths.policy(policy.policy_id)}>
        <span className="s3-policy__head">
          <PolicyIcon categories={benefit?.categories ?? []} />
          <span className="s3-policy__title">
            <span className="s3-policy__name">
              {policyName(policy.policy_id, info)} <span aria-hidden="true">›</span>
            </span>
            {benefit?.benefit_amount?.amount_text ? (
              <span className="s3-policy__summary">{benefit.benefit_amount.amount_text}</span>
            ) : null}
          </span>
          <span className="s3-policy__amount">
            {amount !== null && amount !== undefined ? formatManWon(amount) : '금액 확인 필요'}
            {showOriginal ? <s className="s3-policy__original">{formatManWon(original)}</s> : null}
            {tag !== null ? <span className={`s3-tag s3-tag--${tag.tone}`}>{tag.label}</span> : null}
          </span>
        </span>

        {policy.eligibility.length > 0 ? (
          <span className="s3-policy__chips">
            {policy.eligibility.map((condition) => (
              <StatusChip key={condition.condition_id} status={condition.status}>
                {conditionLabel(condition.condition_id)}
              </StatusChip>
            ))}
          </span>
        ) : null}

        {policy.ai_reason !== null && policy.ai_reason !== undefined && policy.ai_reason !== '' ? (
          <span className="s3-policy__reason">
            {aiGenerated ? <span className="s3-ai">AI</span> : null}
            {policy.ai_reason}
          </span>
        ) : null}
      </a>

      <span className="s3-policy__source">
        출처 {sourceName(policy.source.url)} · {monthDay(policy.source.checked_at)} 확인
        {isStale(policy.source.checked_at) ? ' · ⚠ 최신 내용은 공식 페이지에서 확인' : ''}
      </span>

      <div className="s3-policy__actions">
        <a className="ui-button ui-button--secondary" href={paths.policy(policy.policy_id)}>
          자세히 보기
        </a>
        <a className="ui-button ui-button--primary" href={applyUrl} target="_blank" rel="noopener noreferrer">
          신청하러 가기 ↗
        </a>
      </div>
    </article>
  );
}
