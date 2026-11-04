import type { JSX } from 'react';
import { clearMyData } from '../features/profile/clearMyData.ts';
import { SiteFooter } from '../shared/ui/SiteFooter.tsx';
import { TopBar } from '../shared/ui/TopBar.tsx';

interface PlaceholderLink {
  label: string;
  to: string;
}

interface PlaceholderScreenProps {
  /** 기획서 화면 ID. 예: S1, S2-3 */
  screenId: string;
  title: string;
  /** S1과 L은 뒤로 버튼이 없다 */
  showBack?: boolean;
  links: PlaceholderLink[];
}

/**
 * 아직 만들지 않은 화면 자리. 화면 이동이 맞는지 확인하는 개발용이며,
 * 4단계부터 실제 화면으로 하나씩 바꾼다.
 */
export function PlaceholderScreen({
  screenId,
  title,
  showBack = true,
  links,
}: PlaceholderScreenProps): JSX.Element {
  return (
    <div className="page-width screen">
      <TopBar
        title={title}
        onBack={
          showBack
            ? () => {
                window.history.back();
              }
            : undefined
        }
      />
      <main className="screen__body">
        <p className="screen__eyebrow">{screenId} · 준비 중인 화면 (개발용)</p>
        <h1 className="screen__title">{title}</h1>
        <nav aria-label="이동할 화면" style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          {links.map((link) => (
            <a key={link.to} className="ui-button ui-button--secondary" href={link.to}>
              {link.label}
            </a>
          ))}
        </nav>
      </main>
      <SiteFooter onClearData={clearMyData} />
    </div>
  );
}
