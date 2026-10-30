import type { JSX, ReactNode } from 'react';
import './ui.css';

interface TopBarProps {
  /** 가운데 제목. 예: 조건 고르기 */
  title: string;
  /** 있으면 왼쪽에 뒤로 버튼을 보여 준다 */
  onBack?: () => void;
  /** 오른쪽 자리. 예: 1 / 13, [건너뛰기], [조건 수정] */
  right?: ReactNode;
}

export function TopBar({ title, onBack, right }: TopBarProps): JSX.Element {
  return (
    <header className="ui-topbar">
      {onBack !== undefined ? (
        <button type="button" className="ui-topbar__back" aria-label="뒤로" onClick={onBack}>
          <svg
            width="22"
            height="22"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M15 5l-7 7 7 7" />
          </svg>
        </button>
      ) : null}
      <span className="ui-topbar__title">{title}</span>
      {right !== undefined ? <div className="ui-topbar__right">{right}</div> : null}
    </header>
  );
}
