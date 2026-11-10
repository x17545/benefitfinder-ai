import { useEffect } from 'react';
import type { JSX } from 'react';
import { LoadingScreen } from '../features/benefits/LoadingScreen.tsx';
import { ResultScreen } from '../features/benefits/ResultScreen.tsx';
import { AboutScreen } from '../features/info/AboutScreen.tsx';
import { PrivacyScreen } from '../features/info/PrivacyScreen.tsx';
import { PolicyDetailScreen } from '../features/policy/PolicyDetailScreen.tsx';
import { ConditionScreen } from '../features/profile/ConditionScreen.tsx';
import { loadSavedConditions } from '../features/profile/savedConditions.ts';
import { StartScreen } from '../features/profile/StartScreen.tsx';
import { WhatIfScreen } from '../features/whatIf/WhatIfScreen.tsx';
import { ToastHost } from '../shared/ui/Toast.tsx';
import { PlaceholderScreen } from './PlaceholderScreen.tsx';
import { parseRoute, paths, startLinkInterception } from './router.ts';
import type { Route } from './router.ts';
import { usePathname } from './usePathname.ts';

export function App(): JSX.Element {
  const pathname = usePathname();
  const route = parseRoute(pathname);

  // 같은 사이트 링크는 새로 고침 없이 이동
  useEffect(() => startLinkInterception(), []);

  // 접속할 때 30일 지난 기억하기 저장값을 지운다 (F09-3)
  useEffect(() => {
    loadSavedConditions();
  }, []);

  // 화면이 바뀌면 맨 위로 (기획서 4.1)
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [pathname]);

  return (
    <>
      {renderRoute(route)}
      <ToastHost />
    </>
  );
}

function renderRoute(route: Route): JSX.Element {
  switch (route.name) {
    case 'start':
      return <StartScreen />;
    case 'conditions':
      return <ConditionScreen key={route.step} step={route.step} />;
    case 'loading':
      return <LoadingScreen />;
    case 'result':
      return <ResultScreen />;
    case 'whatIf':
      return <WhatIfScreen />;
    case 'policy':
      return <PolicyDetailScreen key={route.policyId} policyId={route.policyId} />;
    case 'about':
      return <AboutScreen />;
    case 'privacy':
      return <PrivacyScreen />;
    case 'notFound':
      return (
        <PlaceholderScreen
          screenId="P3"
          title="페이지를 찾을 수 없어요"
          links={[{ label: '처음으로', to: paths.start }]}
        />
      );
  }
}
