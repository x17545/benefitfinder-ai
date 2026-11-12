import { useSyncExternalStore } from 'react';
import type { ServiceMeta } from './generated/meta.ts';
import { fetchServiceMeta } from './meta.ts';

export type ServiceMetaState =
  | { status: 'loading' }
  | { status: 'ok'; meta: ServiceMeta }
  | { status: 'error' };

const INITIAL: ServiceMetaState = { status: 'loading' };
let state: ServiceMetaState = INITIAL;
let active = false;
let controller: AbortController | null = null;
const listeners = new Set<() => void>();

function setState(next: ServiceMetaState): void {
  state = next;
  listeners.forEach((listener) => { listener(); });
}

/** 화면 공통의 공개 상태만 조회한다. 사용자 조건·결과는 받거나 저장하지 않는다. */
export async function refreshServiceMeta(): Promise<void> {
  if (!active) return;
  controller?.abort();
  const current = new AbortController();
  controller = current;
  setState(INITIAL);
  try {
    const meta = await fetchServiceMeta({ signal: current.signal });
    if (controller === current && !current.signal.aborted) {
      setState({ status: 'ok', meta });
    }
  } catch {
    if (controller === current && !current.signal.aborted) {
      setState({ status: 'error' });
    }
  } finally {
    if (controller === current) controller = null;
  }
}

/** App이 소유하는 연결. 다시 조회할 때도 오래된 성공값을 재사용하지 않는다. */
export function startServiceMeta(): () => void {
  active = true;
  const onFocus = (): void => { void refreshServiceMeta(); };
  window.addEventListener('focus', onFocus);
  return () => {
    active = false;
    window.removeEventListener('focus', onFocus);
    controller?.abort();
    controller = null;
    setState(INITIAL);
  };
}

export function getServiceMetaState(): ServiceMetaState {
  return state;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

export function useServiceMeta(): ServiceMetaState {
  return useSyncExternalStore(subscribe, getServiceMetaState, () => INITIAL);
}
