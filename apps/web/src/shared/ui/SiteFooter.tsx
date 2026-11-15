import type { JSX } from 'react';
import { refreshServiceMeta, useServiceMeta } from '../api/serviceMetaStore.ts';
import './ui.css';

interface SiteFooterProps {
  /** 기억한 조건과 현재 탭의 입력·결과를 지운다. */
  onClearData?: () => void;
}

export function SiteFooter({ onClearData }: SiteFooterProps): JSX.Element {
  const service = useServiceMeta();
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
      <div className="ui-footer__status" aria-live="polite">
        <div>청년 취업·주거 지원</div>
        {service.status === 'loading' ? <div>자료 상태를 확인하고 있어요.</div> : null}
        {service.status === 'error' ? <div>
          자료 상태를 확인하지 못했어요.{' '}
          <button type="button" className="ui-footer__link" onClick={() => { void refreshServiceMeta(); }}>
            다시 확인
          </button>
        </div> : null}
        {service.status === 'ok' ? <>
          {service.meta.virtual_data === true ? <div>현재 추천은 가상 정책 예제로 계산해요</div> : null}
          <div>
            {service.meta.virtual_data === true ? '가상 자료 기준 시각' : '게시 자료 갱신'}{' '}
            <time dateTime={service.meta.data_updated_at}>{formatMetaTime(service.meta.data_updated_at)} KST</time>
          </div>
          <div>
            {service.meta.virtual_data === true ? '가상 예제 할인율' : '할인율'}{' '}
            {service.meta.base_rate_status === 'verified' && service.meta.base_rate !== null
              ? `연 ${(service.meta.base_rate * 100).toLocaleString('ko-KR', { maximumFractionDigits: 6 })}%`
              : '확인 필요'}
          </div>
          <div>조회 시 남은 분석 {service.meta.daily_remaining.toLocaleString('ko-KR')}회 · 전체 이용자 공유</div>
        </> : null}
      </div>
    </footer>
  );
}

function formatMetaTime(iso: string): string {
  return new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(iso));
}
