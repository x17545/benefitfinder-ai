import type { JSX } from 'react';
import type { AmountBreakdown as AmountBreakdownValue } from '../../shared/api/generated/recommendation-response.ts';
import { formatManWon } from './format.ts';
import './AmountBreakdown.css';

interface AmountBreakdownProps {
  value: AmountBreakdownValue;
  variant?: 'default' | 'compact' | 'hero';
  align?: 'start' | 'end';
  className?: string;
}

/** 서버가 구분한 기간 내 현금 지급 예정액과 만기 전 예상 적립액을 그대로 보여 준다. */
export function AmountBreakdown({
  value,
  variant = 'default',
  align = 'start',
  className,
}: AmountBreakdownProps): JSX.Element {
  const classes = [
    'amount-breakdown',
    `amount-breakdown--${variant}`,
    `amount-breakdown--${align}`,
    className,
  ].filter((item) => item !== undefined && item !== '').join(' ');

  return (
    <span className={classes}>
      <span className="amount-breakdown__item">
        <span>현금 지급 예정(할인 전)</span>
        <b className="amount-breakdown__amount">{formatManWon(value.cash_in_period_won)}</b>
      </span>
      <span className="amount-breakdown__item">
        <span>만기 전 예상 적립</span>
        <b className="amount-breakdown__amount">{formatManWon(value.expected_accrual_won)}</b>
      </span>
    </span>
  );
}
