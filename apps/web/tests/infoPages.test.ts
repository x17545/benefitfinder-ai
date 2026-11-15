import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { AboutScreen } from '../src/features/info/AboutScreen.tsx';
import { PrivacyScreen } from '../src/features/info/PrivacyScreen.tsx';

describe('공통 페이지', () => {
  it('P1 이용 안내는 계산 방식, AI 사용, 출처, 최종 자격, 범위 안내를 보여 준다', () => {
    const html = renderToStaticMarkup(createElement(AboutScreen));
    for (const title of ['어떻게 찾고 계산하나요', 'AI는 어디에 쓰나요', '데이터 출처와 갱신', '최종 자격은 신청 기관에서 확인해 주세요', '다루는 범위와 한계']) {
      expect(html).toContain(title);
    }
    expect(html).toContain('href="/privacy"');
    expect(html).toContain('현재 자료 상태와 갱신 시각');
    expect(html).not.toContain('현재는 가상 정책'); // 조회 전에는 자료 유형을 추정하지 않는다.
    expect(html).not.toContain('매일 새벽 3시에 새로 확인');
  });

  it('P2 개인정보 처리방침은 서버 무저장과 실제 브라우저 보관·삭제 범위를 안내한다', () => {
    const html = renderToStaticMarkup(createElement(PrivacyScreen));
    expect(html).toContain('고른 조건은 서버에 저장하지 않아요');
    expect(html).not.toContain('바로 폐기');
    expect(html).toContain('기억하기를 꺼도 이 탭에는 남아요');
    expect(html).toContain('저장한 지 30일이 지나면 다음 접속 때 지워요');
    expect(html).toContain('현재 구글 애널리틱스 등 이용 통계 도구는 연결하지 않았어요');
    for (const title of ['받는 정보', '서버로 보내는 정보와 보관', '생성형 AI로 보내는 것', '이 기기에 저장되는 것', '이용 통계', '지우는 방법']) {
      expect(html).toContain(title);
    }
    expect(html).toContain('지금 지우기');
  });
});
