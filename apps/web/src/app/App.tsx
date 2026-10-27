import { useEffect, useState } from 'react';
import type { JSX } from 'react';
import { fetchHealth } from '../shared/api/health.js';
import type { HealthResponse } from '../shared/api/health.js';
import { BACKEND_DOCS_URL } from '../shared/config/api.js';

type BackendState =
  | { kind: 'checking'; attempt: number }
  | { kind: 'connected'; data: HealthResponse; attempt: number }
  | { kind: 'failed'; message: string; attempt: number };

function describeError(error: unknown): string {
  if (error instanceof TypeError) {
    return '백엔드에 연결할 수 없습니다. FastAPI가 http://127.0.0.1:8000 에서 실행 중인지 확인하세요.';
  }
  if (error instanceof Error) {
    return error.message;
  }
  return '알 수 없는 오류가 발생했습니다.';
}

export function App(): JSX.Element {
  const [state, setState] = useState<BackendState>({ kind: 'checking', attempt: 1 });
  const [attempt, setAttempt] = useState(1);

  useEffect(() => {
    const controller = new AbortController();
    void fetchHealth({ signal: controller.signal })
      .then((data) => {
        if (!controller.signal.aborted) {
          setState({ kind: 'connected', data, attempt });
        }
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setState({ kind: 'failed', message: describeError(error), attempt });
        }
      });
    return () => { controller.abort(); };
  }, [attempt]);

  function retry(): void {
    setState({ kind: 'checking', attempt: attempt + 1 });
    setAttempt(attempt + 1);
  }

  let statusText = '백엔드 상태 대기 중';
  if (state.kind === 'checking') {
    statusText = "백엔드 연결 확인 중... (시도 " + attempt.toString() + ")";
  } else if (state.kind === 'connected') {
    statusText = "백엔드 연결됨 (/health 응답: " + state.data.status + ")";
  } else if (state.kind === 'failed') {
    statusText = '백엔드 연결 실패';
  }

  const showRetry = state.kind === 'failed' || state.kind === 'connected';

  return (
    <main className="page">
      <h1>BenefitFinder 개발 시작 화면</h1>
      <p>
        여기는 React + TypeScript + Vite 개발용 시작 화면입니다. 실행 주소: <code>http://127.0.0.1:5173</code>
      </p>

      <section aria-live="polite" className="card">
        <h2>백엔드 연결 상태</h2>
        <p data-testid="backend-status">{statusText}</p>
        {state.kind === 'failed' ? (
          <p data-testid="backend-error">{state.message}</p>
        ) : null}
        {showRetry ? (
          <button type="button" onClick={retry}>
            다시 연결 확인
          </button>
        ) : null}
        <p className="hint">
          백엔드가 없어도 이 화면은 동작합니다. 연결 안내만 실패로 표시됩니다.
        </p>
      </section>

      <section className="card">
        <h2>백엔드 API 문서</h2>
        <p>
          <a href={BACKEND_DOCS_URL}>{BACKEND_DOCS_URL}</a>
          <span> (FastAPI 직접 접속, Vite 프록시를 거치지 않습니다)</span>
        </p>
      </section>

      <section className="card">
        <h2>안내</h2>
        <p>
          제품 기능(정책 조회·추천 등)은 아직 구현되지 않았습니다. 가상 정책을 실제처럼 표시하지 않습니다.
        </p>
      </section>
    </main>
  );
}
