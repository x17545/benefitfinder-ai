import type { JSX } from 'react';
import type { TermHelpContent } from '../../../domains/youth/termHelp.ts';

/** 어려운 용어 설명. 눌러서 펼치는 기본 details라 키보드·화면 읽기 도구로도 열 수 있다 */
export function TermHelp({ question, answer }: TermHelpContent): JSX.Element {
  return (
    <details className="s2-term">
      <summary className="s2-term__summary">
        <span className="s2-term__icon" aria-hidden="true">i</span>
        {question}
      </summary>
      <p className="s2-term__answer">{answer}</p>
    </details>
  );
}
