import { useEffect } from 'react';
import type { JSX } from 'react';
import { navigate, paths, CONDITION_STEPS } from '../../app/router.ts';
import { SiteFooter } from '../../shared/ui/SiteFooter.tsx';
import { clearMyData } from '../profile/clearMyData.ts';
import { TopBar } from '../../shared/ui/TopBar.tsx';
import { useAnalysis } from './analysisStore.ts';

/** S3 / S3-E 자리. 7단계에서 실제 결과 화면으로 바꾼다 */
export function ResultScreen(): JSX.Element {
  const analysis = useAnalysis();

  // 결과 없이 주소로 들어오면 처음으로
  useEffect(() => {
    if (analysis.status !== 'done') {
      navigate(paths.start, { replace: true });
    }
  }, [analysis.status]);

  if (analysis.status !== 'done') {
    return <></>;
  }

  const { result, isSample } = analysis;
  const view = result.results['1y'].pv_off;

  return (
    <div className="page-width screen">
      <TopBar
        title="내 지원금 조합"
        onBack={() => {
          navigate(paths.conditions(CONDITION_STEPS));
        }}
        right={
          <a className="s2-skip" href={paths.conditions(CONDITION_STEPS)}>
            조건 수정
          </a>
        }
      />
      <main className="screen__body">
        {isSample ? <p className="s2-dev-note">개발용 예제 결과(contracts/fixtures)예요. 실제 결과가 아니에요.</p> : null}
        <p className="screen__eyebrow">S3 · 준비 중인 화면 (개발용)</p>
        <h1 className="screen__title">결과를 받았어요</h1>
        <p className="s2-desc">
          표시 방식 {result.view} · 1년 기준 조합 {view.portfolios.length}개. 7단계에서 이 결과로 화면을 만들어요.
        </p>
      </main>
      <SiteFooter onClearData={clearMyData} />
    </div>
  );
}
