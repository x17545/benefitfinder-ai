import { useSyncExternalStore } from 'react';
import { subscribeToNavigation } from './router.ts';

function getPathname(): string {
  return window.location.pathname;
}

/** 지금 주소. 화면이 바뀌면 다시 그린다 */
export function usePathname(): string {
  return useSyncExternalStore(subscribeToNavigation, getPathname);
}
