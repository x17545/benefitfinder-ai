import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './app/App.tsx';
import './app/styles/tokens.css';
import './app/styles/global.css';

const rootElement = document.getElementById('root');
if (rootElement === null) {
  throw new Error('root element를 찾을 수 없습니다.');
}

createRoot(rootElement).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
