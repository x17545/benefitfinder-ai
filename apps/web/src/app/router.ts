// 화면 주소 (기획서 4.1 화면 목록) 와 이동 함수.
// 라이브러리 없이 브라우저 주소(history)만 사용한다.

export const CONDITION_STEPS = 13;

export type Route =
  | { name: 'start' }
  | { name: 'conditions'; step: number }
  | { name: 'loading' }
  | { name: 'result' }
  | { name: 'whatIf' }
  | { name: 'policy'; policyId: string }
  | { name: 'about' }
  | { name: 'privacy' }
  | { name: 'notFound' };

export const paths = {
  start: '/',
  conditions: (step: number): string => `/conditions/${String(step)}`,
  loading: '/loading',
  result: '/result',
  whatIf: '/what-if',
  policy: (policyId: string): string => `/policy/${encodeURIComponent(policyId)}`,
  about: '/about',
  privacy: '/privacy',
} as const;

const FIXED_ROUTES: Record<string, Route> = {
  '/': { name: 'start' },
  '/loading': { name: 'loading' },
  '/result': { name: 'result' },
  '/what-if': { name: 'whatIf' },
  '/about': { name: 'about' },
  '/privacy': { name: 'privacy' },
};

/** 주소를 화면 정보로 바꾼다. 모르는 주소는 notFound */
export function parseRoute(pathname: string): Route {
  const path = pathname.length > 1 ? pathname.replace(/\/+$/, '') : pathname;

  const fixed = FIXED_ROUTES[path];
  if (fixed !== undefined) {
    return fixed;
  }

  const conditions = /^\/conditions\/(\d{1,2})$/.exec(path);
  if (conditions !== null) {
    const step = Number(conditions[1]);
    if (step >= 1 && step <= CONDITION_STEPS) {
      return { name: 'conditions', step };
    }
    return { name: 'notFound' };
  }

  const policy = /^\/policy\/([^/]+)$/.exec(path);
  if (policy !== null) {
    try {
      return { name: 'policy', policyId: decodeURIComponent(policy[1]) };
    } catch {
      return { name: 'notFound' };
    }
  }

  return { name: 'notFound' };
}

const NAVIGATE_EVENT = 'app:navigate';

/** 다른 화면으로 이동한다. replace면 뒤로 가기 기록을 남기지 않는다 */
export function navigate(path: string, options: { replace?: boolean } = {}): void {
  if (options.replace === true) {
    window.history.replaceState(null, '', path);
  } else {
    window.history.pushState(null, '', path);
  }
  window.dispatchEvent(new Event(NAVIGATE_EVENT));
}

/** 주소가 바뀔 때 알려 준다 (navigate, 브라우저 뒤로·앞으로) */
export function subscribeToNavigation(callback: () => void): () => void {
  window.addEventListener('popstate', callback);
  window.addEventListener(NAVIGATE_EVENT, callback);
  return () => {
    window.removeEventListener('popstate', callback);
    window.removeEventListener(NAVIGATE_EVENT, callback);
  };
}

/**
 * 같은 사이트 안의 <a href> 클릭을 새로 고침 없이 화면 이동으로 바꾼다.
 * 새 탭(target), 다운로드, Cmd·Ctrl 클릭, 다른 사이트 링크는 그대로 둔다.
 */
export function startLinkInterception(): () => void {
  function onClick(event: MouseEvent): void {
    if (event.defaultPrevented || event.button !== 0) {
      return;
    }
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
      return;
    }
    if (!(event.target instanceof Element)) {
      return;
    }
    const anchor = event.target.closest('a');
    if (anchor === null || anchor.target !== '' || anchor.hasAttribute('download')) {
      return;
    }
    const url = new URL(anchor.href);
    if (url.origin !== window.location.origin) {
      return;
    }
    event.preventDefault();
    navigate(url.pathname + url.search + url.hash);
  }

  document.addEventListener('click', onClick);
  return () => {
    document.removeEventListener('click', onClick);
  };
}
