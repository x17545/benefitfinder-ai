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
    expect(html).toContain('가상 정책으로 조합 계산을 시험하는 개발 버전');
    expect(html).not.toContain('매일 새벽 3시에 새로 확인');
  });

  it('P2는 서버 무저장, 탭 보관, 선택한 30일 저장과 지우는 방법을 구분한다', () => {
    const html = renderToStaticMarkup(createElement(PrivacyScreen));
    expect(html).toContain('고른 조건은 서버에 저장하지 않아요');
    expect(html).toContain('입력한 조건과 마지막 결과');
    expect(html).toContain('이 탭에 보관해요');
    expect(html).toContain('30일');
    expect(html).toContain('이용 통계 도구는 연결하지 않았어요');
    for (const title of ['받는 정보', '서버로 보내는 정보와 폐기', '생성형 AI로 보내는 것', '이 기기에 저장되는 것', '이용 통계', '지우는 방법']) {
      expect(html).toContain(title);
    }
    expect(html).toContain('지금 지우기');
  });
});
