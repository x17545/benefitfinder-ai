import type { JSX } from 'react';
import type { TermHelpContent } from '../../../domains/youth/termHelp.ts';

/**
 * 어려운 용어 설명. 한 줄 뜻은 늘 보이고, [자세히]를 누르면 설명이 펼쳐진다.
 * 기본 details라 키보드·화면 읽기 도구로도 열 수 있다.
 */
export function TermHelp({ term, short, answer }: TermHelpContent): JSX.Element {
  return (
    <details className="s2-term">
      <summary className="s2-term__summary">
        <span className="s2-term__icon" aria-hidden="true">i</span>
        <span className="s2-term__short">
          <b>{term}</b> {short}
        </span>
        <span className="s2-term__more" aria-hidden="true">
          <span className="s2-term__open">자세히</span>
          <span className="s2-term__close">접기</span>
        </span>
      </summary>
      <p className="s2-term__answer">{answer}</p>
    </details>
  );
}
