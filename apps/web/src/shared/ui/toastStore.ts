import { useSyncExternalStore } from 'react';

/** 짧은 알림이 떠 있는 시간 (기획서 4.8 토스트) */
const TOAST_MS = 1800;

let message: string | null = null;
let timer: ReturnType<typeof setTimeout> | undefined;
const listeners = new Set<() => void>();

function emit(): void {
  listeners.forEach((listener) => {
    listener();
  });
}

/** 화면 아래에 1.8초 떴다 사라지는 알림 */
export function showToast(text: string): void {
  message = text;
  emit();
  clearTimeout(timer);
  timer = setTimeout(() => {
    message = null;
    emit();
  }, TOAST_MS);
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function getMessage(): string | null {
  return message;
}

export function useToastMessage(): string | null {
  return useSyncExternalStore(subscribe, getMessage);
}
