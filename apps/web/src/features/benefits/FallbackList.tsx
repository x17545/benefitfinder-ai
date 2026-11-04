import type { JSX } from 'react';
import { paths } from '../../app/router.ts';
import type { NormalizedBenefit } from '../../shared/api/benefits.ts';
import { formatManWon, recruitTag } from './format.ts';
import { policyName } from './resultModel.ts';
import type { ResultView } from './resultModel.ts';

type FallbackItem = NonNullable<ResultView['fallback_list']>[number];

interface FallbackListProps {
  items: readonly FallbackItem[];
  info: ReadonlyMap<string, NormalizedBenefit>;
}

/** 조합을 못 만들 때: 받을 수 있는 정책 최대 5개 목록 (기획서 4.5 예외 화면) */
export function FallbackList({ items, info }: FallbackListProps): JSX.Element {
  return (
    <div className="s3-policies">
      {items.map((item) => {
        const benefit = info.get(item.policy_id);
        const tag = benefit !== undefined ? recruitTag(benefit.application) : null;
        const applyUrl = benefit?.application.url ?? benefit?.source.url ?? null;
        return (
          <article key={item.policy_id} className="s3-policy">
            <a className="s3-policy__main" href={paths.policy(item.policy_id)}>
              <span className="s3-policy__head">
                <span className="s3-policy__title">
                  <span className="s3-policy__name">
                    {policyName(item.policy_id, info)} <span aria-hidden="true">›</span>
                  </span>
                  {benefit?.benefit_amount?.amount_text ? (
                    <span className="s3-policy__summary">{benefit.benefit_amount.amount_text}</span>
                  ) : null}
                </span>
                <span className="s3-policy__amount">
                  {item.amount_in_period_won !== null && item.amount_in_period_won !== undefined
                    ? formatManWon(item.amount_in_period_won)
                    : '금액 확인 필요'}
                  {tag !== null ? <span className={`s3-tag s3-tag--${tag.tone}`}>{tag.label}</span> : null}
                </span>
              </span>
              {item.reason !== undefined && item.reason !== '' ? (
                <span className="s3-policy__reason">{item.reason}</span>
              ) : null}
            </a>
            <div className="s3-policy__actions">
              <a className="ui-button ui-button--secondary" href={paths.policy(item.policy_id)}>
                자세히 보기
              </a>
              {applyUrl !== null ? (
                <a className="ui-button ui-button--primary" href={applyUrl} target="_blank" rel="noopener noreferrer">
                  신청하러 가기 ↗
                </a>
              ) : null}
            </div>
          </article>
        );
      })}
    </div>
  );
}
