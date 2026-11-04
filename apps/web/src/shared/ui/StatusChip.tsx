import type { JSX, ReactNode } from 'react';
import './ui.css';

/** 서버가 보내는 판정 상태 이름 그대로 (contracts/v1) */
export type EligibilityStatus = 'met' | 'not_met' | 'needs_review';

const SYMBOL: Record<EligibilityStatus, string> = {
  met: '✓',
  not_met: '✕',
  needs_review: '?',
};

const SPOKEN: Record<EligibilityStatus, string> = {
  met: '충족',
  not_met: '미충족',
  needs_review: '확인 필요',
};

interface StatusChipProps {
  status: EligibilityStatus;
  /** 조건 이름. 예: 나이, 무주택 */
  children: ReactNode;
}

export function StatusChip({ status, children }: StatusChipProps): JSX.Element {
  return (
    <span className={`ui-status ui-status--${status}`}>
      <span aria-hidden="true">{SYMBOL[status]}</span>
      <span>{children}</span>
      <span className="ui-sr-only">{SPOKEN[status]}</span>
    </span>
  );
}
