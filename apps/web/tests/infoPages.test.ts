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
  });

  it('P2 개인정보 처리방침은 분석 후 바로 폐기한다는 안내와 지우는 방법을 보여 준다', () => {
    const html = renderToStaticMarkup(createElement(PrivacyScreen));
    expect(html).toContain('분석이 끝나면 바로 폐기해요');
    for (const title of ['받는 정보', '서버로 보내는 정보와 폐기', '생성형 AI로 보내는 것', '이 기기에 저장되는 것', '이용 통계', '지우는 방법']) {
      expect(html).toContain(title);
    }
    expect(html).toContain('지금 지우기');
  });
});
