import type { JSX } from 'react';
import { paths } from '../../app/router.ts';
import { InfoCard, InfoLayout } from './InfoLayout.tsx';

/** P1 이용 안내. 현재 실행 범위와 목표 기능을 구분한다. */
export function AboutScreen(): JSX.Element {
  return (
    <InfoLayout
      title="이용 안내"
      heading="청년 정책 매니저는 이렇게 계산해요"
      lead="현재는 가상 정책으로 조합 계산을 시험하는 개발 버전이에요. 실제 신청 가능한 정책 추천은 준비 중이에요."
    >
      <div className="info-cards">
        <InfoCard title="어떻게 찾고 계산하나요">
          <p>
            목표는 고른 조건으로 검수한 정책을 찾고, 조건마다 충족 · 미충족 · 확인 필요를 판정하는 거예요. 함께 받을 수
            없는 정책은 빼고, 총액이 큰 조합을 최대 3개까지 보여 드려요.
          </p>
          <p>자격 판정, 금액, 조합, 현재가치는 모두 정해진 규칙(코드)으로 계산해요.</p>
        </InfoCard>

        <InfoCard title="AI는 어디에 쓰나요">
          <ul>
            <li>데이터 준비 도구에서 정책 공고의 조건 · 금액 · 중복 제한 후보를 찾아요. 검수 후보는 실제 추천에 자동 반영하지 않아요.</li>
            <li>AI 설명을 켠 환경에서는 정책 조항으로 안내 문장을 써요. AI가 쓴 문장에는 [AI] 표시를 붙이고, 실패하면 기본 안내를 보여 드려요.</li>
            <li>금액과 자격 같은 숫자 계산은 AI가 하지 않아요.</li>
          </ul>
        </InfoCard>

        <InfoCard title="데이터 출처와 갱신">
          <p>
            현재 추천 결과의 정책과 금액은 가상 자료예요. 온통청년 수집 도구와 새벽 3시 실행 설정은
            준비했지만, 서비스에서 매일 갱신되는 실제 정책 추천은 아직 제공하지 않아요.
          </p>
        </InfoCard>

        <InfoCard title="최종 자격은 신청 기관에서 확인해 주세요">
          <p>
            결과는 입력한 조건으로 계산한 참고 결과예요. 실제 자격과 최신 조건은 정책을 운영하는 기관에서
            확인해 주세요.
          </p>
        </InfoCard>

        <InfoCard title="다루는 범위와 한계">
          <ul>
            <li>지금은 만 19 ~ 39세 청년의 취업 · 주거 지원을 다뤄요.</li>
            <li>
              공식 근거가 없는 조건이나, 함께 받을 수 있는지 확인되지 않은 정책은 &lsquo;확인 필요&rsquo;로
              표시해요.
            </li>
            <li>생활 · 자산형성 · 교육 · 장학 지원은 아직 다루지 않아요.</li>
          </ul>
        </InfoCard>

        <InfoCard title="내 정보는 어떻게 다루나요">
          <p>고른 조건은 서버에 저장하지 않아요. 이 탭에는 입력과 결과를 보관하고, 기억하기를 켜면 조건을 30일 동안 이 브라우저에 저장해요.</p>
          <a className="ui-button ui-button--secondary" href={paths.privacy}>
            개인정보 처리방침 보기
          </a>
        </InfoCard>
      </div>
    </InfoLayout>
  );
}
