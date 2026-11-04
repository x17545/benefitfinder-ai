import type { JSX } from 'react';
import './ui.css';

interface StepProgressProps {
  /** 지금 단계 (1부터) */
  current: number;
  /** 전체 단계 수. 조건 고르기는 13 */
  total: number;
}

export function StepProgress({ current, total }: StepProgressProps): JSX.Element {
  const percent = total > 0 ? Math.min(100, (current / total) * 100) : 0;

  return (
    <div
      className="ui-progress"
      role="progressbar"
      aria-label="조건 고르기 진행"
      aria-valuemin={1}
      aria-valuemax={total}
      aria-valuenow={current}
    >
      <div className="ui-progress__fill" style={{ width: `${percent.toFixed(1)}%` }} />
    </div>
  );
}
