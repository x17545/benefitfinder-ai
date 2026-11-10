import type { JSX } from 'react';
import './ui.css';

interface SiteFooterProps {
  /** [내 정보 지우기]를 눌렀을 때. 기억하기 기능을 만들 때 연결한다 */
  onClearData?: () => void;
}

export function SiteFooter({ onClearData }: SiteFooterProps): JSX.Element {
  return (
    <footer className="ui-footer">
      <div className="ui-footer__links">
        <a className="ui-footer__link" href="/about">
          이용 안내
        </a>
        <a className="ui-footer__link" href="/privacy">
          개인정보 처리방침
        </a>
        <button type="button" className="ui-footer__link" onClick={onClearData}>
          내 정보 지우기
        </button>
      </div>
      <div>청년 취업·주거 지원 · 현재 추천은 가상 정책 예제로 계산해요</div>
    </footer>
  );
}
