import type { JSX, ReactNode } from 'react';
import { navigate, paths } from '../../app/router.ts';
import { SiteFooter } from '../../shared/ui/SiteFooter.tsx';
import { TopBar } from '../../shared/ui/TopBar.tsx';
import { clearMyData } from '../profile/clearMyData.ts';
import './InfoPage.css';

interface InfoLayoutProps {
  /** 상단 바 제목. 예: 이용 안내 */
  title: string;
  /** 큰 제목 */
  heading: string;
  /** 큰 제목 아래 한두 줄 */
  lead: string;
  children: ReactNode;
}

/** 이전 화면이 없으면(주소로 바로 들어오면) S1로 간다 */
function goBack(): void {
  if (window.history.length > 1) {
    window.history.back();
    return;
  }
  navigate(paths.start);
}

/** 공통 페이지(P1 이용 안내, P2 개인정보 처리방침) 틀 (기획서 4.8) */
export function InfoLayout({ title, heading, lead, children }: InfoLayoutProps): JSX.Element {
  return (
    <div className="page-width screen">
      <TopBar title={title} onBack={goBack} />
      <main className="screen__body info-body">
        <div className="info-head">
          <h1 className="screen__title">{heading}</h1>
          <p className="info-lead">{lead}</p>
        </div>
        {children}
        <div className="info-actions">
          <a className="ui-button ui-button--secondary" href={paths.start}>
            처음으로
          </a>
        </div>
      </main>
      <SiteFooter onClearData={clearMyData} />
    </div>
  );
}

interface InfoCardProps {
  title: string;
  children: ReactNode;
}

/** 안내 카드 하나: 제목 + 내용 */
export function InfoCard({ title, children }: InfoCardProps): JSX.Element {
  return (
    <section className="ui-card info-card">
      <h2 className="info-card__title">{title}</h2>
      {children}
    </section>
  );
}
