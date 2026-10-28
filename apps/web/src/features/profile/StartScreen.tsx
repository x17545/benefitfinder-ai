import { useEffect, useState } from 'react';
import type { JSX } from 'react';
import { navigate, paths } from '../../app/router.ts';
import { Button } from '../../shared/ui/Button.tsx';
import { SiteFooter } from '../../shared/ui/SiteFooter.tsx';
import { clearMyData } from './clearMyData.ts';
import { updateFlowState, useFlowState } from './flowStore.ts';
import { SavedConditionsCard } from './SavedConditionsCard.tsx';
import { loadSavedConditions } from './savedConditions.ts';
import './StartScreen.css';

/** S1 시작 (기획서 4.2, 문구 S1-01 ~ S1-BTN) */
export function StartScreen(): JSX.Element {
  const { consent, remember } = useFlowState();
  const [saved, setSaved] = useState(() => loadSavedConditions());

  // 기억해 둔 조건이 있으면 기억하기를 켠 채로 보여 준다
  useEffect(() => {
    if (saved !== null) {
      updateFlowState({ remember: true });
    }
  }, [saved]);

  function clear(): void {
    clearMyData();
    setSaved(null);
  }

  return (
    <div className="page-width screen">
      <header className="s1-header">
        <a className="s1-brand" href={paths.start}>
          <span className="s1-brand__mark" aria-hidden="true">
            ₩
          </span>
          청년 정책 매니저
        </a>
      </header>

      <main className="screen__body" style={{ gap: 0 }}>
        {saved !== null ? <SavedConditionsCard saved={saved} onClear={clear} /> : null}

        <section className="s1-intro">
          <h1 className="s1-intro__title">받을 수 있는 지원금, 가장 이득인 조합으로 찾아 드려요</h1>
          <p className="s1-intro__desc">함께 받을 수 없는 정책은 빼고, 총액이 가장 큰 조합을 계산해요.</p>
        </section>

        <ol className="s1-steps ui-card">
          <li className="s1-steps__item">
            <span className="s1-steps__num" aria-hidden="true">
              1
            </span>
            <span>
              내 조건을 골라요 <span className="s1-steps__time">(약 3분)</span>
            </span>
          </li>
          <li className="s1-steps__item">
            <span className="s1-steps__num" aria-hidden="true">
              2
            </span>
            <span>함께 받을 수 있는 조합과 총액을 봐요</span>
          </li>
          <li className="s1-steps__item">
            <span className="s1-steps__num" aria-hidden="true">
              3
            </span>
            <span>신청 방법과 준비 서류까지 확인해요</span>
          </li>
        </ol>

        <p className="s1-notice">
          선택한 조건으로 정책을 찾고 지원금 조합을 계산해요. 결과는 참고용이며 최종 자격은 신청 기관에서
          확인해야 해요. <a href={paths.about}>자세히</a>
        </p>

        <div className="s1-actions">
          <div className="s1-consents">
            <div className="s1-consent">
              <input
                id="s1-consent"
                type="checkbox"
                checked={consent}
                onChange={(event) => {
                  updateFlowState({ consent: event.target.checked });
                }}
              />
              <label htmlFor="s1-consent">
                <b>(필수)</b> 선택한 조건은 정책 분석에만 사용돼요
              </label>
              <a className="s1-consent__link" href={paths.privacy}>
                개인정보 처리방침
              </a>
            </div>
            <div className="s1-consent">
              <input
                id="s1-remember"
                type="checkbox"
                checked={remember}
                onChange={(event) => {
                  updateFlowState({ remember: event.target.checked });
                }}
              />
              <label htmlFor="s1-remember">
                (선택) 이 기기에 내 조건 기억하기
                <span className="s1-consent__hint">30일 동안, 언제든 지울 수 있어요</span>
              </label>
            </div>
          </div>

          <Button
            size="large"
            block
            disabled={!consent}
            onClick={() => {
              navigate(paths.conditions(1));
            }}
          >
            시작하기
          </Button>
        </div>
      </main>

      <SiteFooter onClearData={clear} />
    </div>
  );
}
