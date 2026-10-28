// 개발용 미리보기: http://127.0.0.1:5173/ui-preview.html
// 공통 부품이 어떻게 보이는지 확인하는 화면이다. 실제 서비스 화면이 아니며, 화면을 다 만들면 지운다.
import { StrictMode, useState } from 'react';
import type { JSX } from 'react';
import { createRoot } from 'react-dom/client';
import './styles/tokens.css';
import './styles/global.css';
import { Button } from '../shared/ui/Button.tsx';
import { ChoiceChip, ChoiceGroup } from '../shared/ui/ChoiceChip.tsx';
import { SiteFooter } from '../shared/ui/SiteFooter.tsx';
import { StatusChip } from '../shared/ui/StatusChip.tsx';
import { StepProgress } from '../shared/ui/StepProgress.tsx';
import { TopBar } from '../shared/ui/TopBar.tsx';

const LIVING = ['혼자', '부모님과 함께', '배우자·가족과', '기숙사·고시원', '기타'];
const MILITARY = ['해당 없음', '1년 6개월 이하', '1년 6개월 초과 ~ 2년', '2년 초과'];
const SPECIAL = ['기초생활수급자', '차상위계층', '한부모가정', '해당 없음'];

export function Preview(): JSX.Element {
  const [living, setLiving] = useState('혼자');
  const [military, setMilitary] = useState<string | null>(null);
  const [special, setSpecial] = useState<string[]>([]);

  function toggleSpecial(item: string): void {
    setSpecial((prev) => (prev.includes(item) ? prev.filter((v) => v !== item) : [...prev, item]));
  }

  return (
    <div className="page-width" style={{ display: 'flex', flexDirection: 'column', gap: 24, paddingBlock: 16 }}>
      <TopBar title="조건 고르기" onBack={() => undefined} right="1 / 13" />
      <StepProgress current={1} total={13} />

      <section style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <h2 style={{ margin: 0, fontSize: 17 }}>줄 선택지 (하나 고르기)</h2>
        <ChoiceGroup label="사는 형태" layout="rows">
          {LIVING.map((item) => (
            <ChoiceChip key={item} layout="row" selected={living === item} onSelect={() => { setLiving(item); }}>
              {item}
            </ChoiceChip>
          ))}
        </ChoiceGroup>
      </section>

      <section style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <h2 style={{ margin: 0, fontSize: 17 }}>칩 선택지 (하나 고르기)</h2>
        <ChoiceGroup label="군 복무 기간">
          {MILITARY.map((item) => (
            <ChoiceChip key={item} selected={military === item} onSelect={() => { setMilitary(item); }}>
              {item}
            </ChoiceChip>
          ))}
        </ChoiceGroup>
      </section>

      <section style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <h2 style={{ margin: 0, fontSize: 17 }}>칩 선택지 (여러 개 고르기)</h2>
        <ChoiceGroup label="해당 조건" kind="checkbox">
          {SPECIAL.map((item) => (
            <ChoiceChip key={item} kind="checkbox" selected={special.includes(item)} onSelect={() => { toggleSpecial(item); }}>
              {item}
            </ChoiceChip>
          ))}
        </ChoiceGroup>
      </section>

      <section className="ui-card" style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        <StatusChip status="met">나이</StatusChip>
        <StatusChip status="not_met">재학 중</StatusChip>
        <StatusChip status="needs_review">부모님 소득 확인 필요</StatusChip>
      </section>

      <section style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
          <Button variant="secondary">자세히 보기</Button>
          <Button>신청하러 가기 ↗</Button>
        </div>
        <Button size="large" block disabled>
          다음 (비활성)
        </Button>
        <Button size="large" block>
          시작하기
        </Button>
      </section>

      <SiteFooter />
    </div>
  );
}

const rootElement = document.getElementById('root');
if (rootElement === null) {
  throw new Error('root element를 찾을 수 없습니다.');
}

createRoot(rootElement).render(
  <StrictMode>
    <Preview />
  </StrictMode>,
);
