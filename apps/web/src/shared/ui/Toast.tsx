import type { JSX } from 'react';
import { useToastMessage } from './toastStore.ts';
import './ui.css';

/** 앱에 하나만 둔다. showToast()로 띄운다 */
export function ToastHost(): JSX.Element {
  const message = useToastMessage();
  return (
    <div className="ui-toast-host" role="status" aria-live="polite">
      {message !== null ? <div className="ui-toast">{message}</div> : null}
    </div>
  );
}
