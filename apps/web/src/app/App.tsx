import { useEffect } from 'react';
import type { JSX } from 'react';
import { LoadingScreen } from '../features/benefits/LoadingScreen.tsx';
import { ResultScreen } from '../features/benefits/ResultScreen.tsx';
import { ConditionScreen } from '../features/profile/ConditionScreen.tsx';
import { StartScreen } from '../features/profile/StartScreen.tsx';
import { PlaceholderScreen } from './PlaceholderScreen.tsx';
import { parseRoute, paths, startLinkInterception } from './router.ts';
import type { Route } from './router.ts';
import { usePathname } from './usePathname.ts';

export function App(): JSX.Element {
  const pathname = usePathname();
  const route = parseRoute(pathname);

  // 같은 사이트 링크는 새로 고침 없이 이동
  useEffect(() => startLinkInterception(), []);

  // 화면이 바뀌면 맨 위로 (기획서 4.1)
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [pathname]);

  return renderRoute(route);
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
      return <PlaceholderScreen screenId="S4" title="상황이 바뀌면?" links={[]} />;
    case 'policy':
      return <PlaceholderScreen screenId="S5" title="정책 상세" links={[]} />;
    case 'about':
      return <PlaceholderScreen screenId="P1" title="이용 안내" links={[]} />;
    case 'privacy':
      return <PlaceholderScreen screenId="P2" title="개인정보 처리방침" links={[]} />;
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
