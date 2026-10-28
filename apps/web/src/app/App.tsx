import { useEffect } from 'react';
import type { JSX } from 'react';
import { PlaceholderScreen } from './PlaceholderScreen.tsx';
import { CONDITION_STEPS, parseRoute, paths, startLinkInterception } from './router.ts';
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
      return (
        <PlaceholderScreen
          screenId="S1"
          title="시작"
          showBack={false}
          links={[{ label: '시작하기', to: paths.conditions(1) }]}
        />
      );
    case 'conditions': {
      const { step } = route;
      const links: { label: string; to: string }[] = [];
      if (step > 1) {
        links.push({ label: '이전 단계', to: paths.conditions(step - 1) });
      }
      links.push(
        step < CONDITION_STEPS
          ? { label: '다음', to: paths.conditions(step + 1) }
          : { label: '내 조합 찾기', to: paths.loading },
      );
      return (
        <PlaceholderScreen
          key={step}
          screenId={`S2-${String(step)}`}
          title={`조건 고르기 ${String(step)} / ${String(CONDITION_STEPS)}`}
          links={links}
        />
      );
    }
    case 'loading':
      return (
        <PlaceholderScreen
          screenId="L"
          title="로딩"
          showBack={false}
          links={[{ label: '결과 보기', to: paths.result }]}
        />
      );
    case 'result':
      return (
        <PlaceholderScreen
          screenId="S3 / S3-E"
          title="내 지원금 조합"
          links={[
            { label: '조건 수정', to: paths.conditions(CONDITION_STEPS) },
            { label: '상황이 바뀌면 어떻게 될까?', to: paths.whatIf },
          ]}
        />
      );
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
