import { useEffect } from 'react';
import type { JSX } from 'react';
import { navigate, paths } from '../../app/router.ts';
import { Button } from '../../shared/ui/Button.tsx';
import { missingSteps } from '../profile/answerRules.ts';
import { getFlowState } from '../profile/flowStore.ts';
import { removeSavedConditions, saveConditions } from '../profile/savedConditions.ts';
import { toRecommendationRequest } from '../profile/toRequest.ts';
import {
  cancelAnalysis,
  getAnalysisState,
  showSampleResult,
  startAnalysis,
  useAnalysis,
} from './analysisStore.ts';
import './LoadingScreen.css';

function start(): void {
  const { answers, remember } = getFlowState();
  const request = toRecommendationRequest(answers);
  // 기억하기를 켰을 때만 분석을 시작하는 순간 저장한다. 끄면 남아 있던 것도 지운다 (F09)
  if (remember) {
    saveConditions(request.profile);
  } else {
    removeSavedConditions();
  }
  startAnalysis(request);
}

/** L 로딩 (기획서 4.4). 상단 바와 뒤로 버튼이 없다 */
export function LoadingScreen(): JSX.Element {
  const analysis = useAnalysis();

  // 들어오면 분석을 시작하고, 나가면(뒤로 가기) 취소한다
  useEffect(() => {
    const { consent, answers } = getFlowState();
    if (!consent) {
      navigate(paths.start, { replace: true });
      return;
    }
    if (missingSteps(answers).length > 0) {
      navigate(paths.conditions(13), { replace: true });
      return;
    }
    start();
    return () => {
      cancelAnalysis();
    };
  }, []);

  // 끝나면 결과로. 뒤로 가기를 누르면 로딩이 아니라 확인(13단계)으로 가도록 기록을 바꾼다
  useEffect(() => {
    // 지난 결과가 남아 있던 첫 그림이 아니라, 이번 분석이 끝났을 때만 이동한다
    if (analysis.status === 'done' && getAnalysisState().status === 'done') {
      navigate(paths.result, { replace: true });
    }
  }, [analysis.status]);

  if (analysis.status === 'failed' && analysis.reason === 'limit') {
    return (
      <div className="page-width screen">
        <main className="l-body">
          <h1 className="screen__title">오늘 이용량이 많아 잠시 쉬어요</h1>
          <p className="s2-desc">내일 다시 이용해 주세요</p>
          <div className="l-actions">
            <Button
              onClick={() => {
                navigate(paths.start);
              }}
            >
              처음으로
            </Button>
          </div>
        </main>
      </div>
    );
  }

  if (analysis.status === 'failed') {
    return (
      <div className="page-width screen">
        <main className="l-body">
          <h1 className="screen__title">분석에 실패했어요</h1>
          <p className="s2-desc">잠시 후 다시 시도해 주세요. 고른 조건은 그대로 남아 있어요.</p>
          <div className="l-actions">
            <Button onClick={start}>다시 시도</Button>
            <Button
              variant="secondary"
              onClick={() => {
                navigate(paths.conditions(13));
              }}
            >
              조건 수정
            </Button>
          </div>
          {import.meta.env.DEV ? (
            <div className="l-dev">
              <span>개발용: 백엔드가 꺼져 있으면 contracts 예제 결과로 다음 화면을 볼 수 있어요.</span>
              <Button
                variant="secondary"
                onClick={() => {
                  void showSampleResult();
                }}
              >
                예제 결과로 보기
              </Button>
            </div>
          ) : null}
        </main>
      </div>
    );
  }

  const progress = analysis.status === 'running' ? analysis.progress : [];

  return (
    <div className="page-width screen">
      <main className="l-body" aria-live="polite">
        <div className="l-spinner" aria-hidden="true" />
        <h1 className="screen__title">가장 이득인 조합을 계산하고 있어요</h1>
        <p className="s2-desc">아래 단계로 정책을 찾고 계산하고 있어요</p>
        {progress.length > 0 ? (
          <ol className="l-steps">
            {progress.map((item) => (
              <li key={item.step} className={`l-step l-step--${item.state}`}>
                <span className="l-step__mark" aria-hidden="true">
                  {item.state === 'done' ? '✓' : item.state === 'failed' ? '✕' : ''}
                </span>
                <span>{item.label}</span>
                {item.detail !== undefined && item.detail !== null ? (
                  <span className="l-step__detail">{item.detail}</span>
                ) : null}
              </li>
            ))}
          </ol>
        ) : null}
      </main>
    </div>
  );
}
