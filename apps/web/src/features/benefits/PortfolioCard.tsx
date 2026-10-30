import type { JSX } from 'react';
import type { NormalizedBenefit } from '../../shared/api/benefits.ts';
import { formatManWon } from './format.ts';
import { policyName, portfolioLabel } from './resultModel.ts';
import type { Portfolio } from './resultModel.ts';

const BAR_COLORS = ['var(--color-bar-1)', 'var(--color-bar-2)', 'var(--color-bar-3)'];

interface PortfolioCardProps {
  portfolio: Portfolio;
  /** 1위 총액. 막대 전체 길이 = 이 조합 총액 ÷ 1위 총액 */
  topTotal: number;
  selected: boolean;
  onSelect: () => void;
  info: ReadonlyMap<string, NormalizedBenefit>;
}

/** 조합 카드 (S3 5 ~ 7번): 배지 + 총액 + 구성 + 포기하는 지원금 + 금액 비율 막대 */
export function PortfolioCard({ portfolio, topTotal, selected, onSelect, info }: PortfolioCardProps): JSX.Element {
  const unverified = portfolio.unverified_pairs?.length ?? 0;
  const givenUp = portfolio.given_up ?? [];
  const width = topTotal > 0 ? Math.min(100, (portfolio.total_won / topTotal) * 100) : 100;

  return (
    <button
      type="button"
      aria-pressed={selected}
      className={selected ? 's3-combo s3-combo--selected' : 's3-combo'}
      onClick={onSelect}
    >
      <span className={portfolio.rank === 1 ? 's3-badge s3-badge--top' : 's3-badge'}>
        {portfolioLabel(portfolio.rank)}
      </span>
      <span className="s3-combo__total">{formatManWon(portfolio.total_won)}</span>
      <span className="s3-combo__names">
        {portfolio.policies.map((policy) => policyName(policy.policy_id, info)).join(' + ')}
      </span>
      {givenUp.length > 0 ? (
        <span className="s3-combo__give">
          포기하는 지원금 ·{' '}
          {givenUp
            .map(
              (item) =>
                `${policyName(item.policy_id, info)}${
                  item.amount_won !== null && item.amount_won !== undefined ? ` ${formatManWon(item.amount_won)}` : ''
                }`,
            )
            .join(', ')}
        </span>
      ) : null}
      {unverified > 0 ? <span className="s3-combo__check">? 확인 필요 {unverified}건</span> : null}
      <span className="s3-bar" style={{ width: `${width.toFixed(1)}%` }} aria-hidden="true">
        {portfolio.policies.map((policy, index) => (
          <span
            key={policy.policy_id}
            style={{
              flexGrow: policy.amount_in_period_won ?? 0,
              background: BAR_COLORS[index % BAR_COLORS.length],
            }}
          />
        ))}
      </span>
    </button>
  );
}
