import { useState } from 'react';
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
          현재는 가상 정책으로 조합 계산을 시험해요. 실제 받을 수 있는 지원금이 아니에요.{' '}
          <a href={paths.about}>자세히</a>
        </p>

        <div className="s1-actions">
          {/* 휴대폰에서는 아래 .s1-start(필수 동의 + 시작하기)만 화면 아래에 붙고, 기억하기와 보관 안내는 본문에 남는다 */}
          <p id="s1-consent-note" className="s1-consent-note">
            입력과 결과는 새로고침을 위해 이 탭에 임시 보관해요
          </p>
          <div className="s1-consent s1-remember">
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

          <div className="s1-start">
            <div className="s1-consent s1-required">
              <input
                id="s1-consent"
                type="checkbox"
                checked={consent}
                aria-describedby="s1-consent-note"
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
        </div>
      </main>

      <SiteFooter onClearData={clear} />
    </div>
  );
}
