import type { JSX, ReactNode } from 'react';

interface PrivacyNoteProps {
  children: ReactNode;
}

/** 민감한 조건을 묻는 단계(8·9·12)의 서버 저장·브라우저 삭제 안내. */
export function PrivacyNote({ children }: PrivacyNoteProps): JSX.Element {
  return (
    <p className="s2-private">
      <svg
        width="16"
        height="16"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <rect x="5" y="11" width="14" height="10" rx="2" />
        <path d="M8 11V8a4 4 0 0 1 8 0v3" />
      </svg>
      <span>{children}</span>
    </p>
  );
}
