import type { JSX } from 'react';
import { paths } from '../../app/router.ts';
import { InfoCard, InfoLayout } from './InfoLayout.tsx';

/** P1 이용 안내 (기획서 4.8, v3.0 카드 5개) */
export function AboutScreen(): JSX.Element {
  return (
    <InfoLayout
      title="이용 안내"
      heading="청년 정책 매니저는 이렇게 계산해요"
      lead="고른 조건으로 받을 수 있는 지원금을 찾고, 함께 받을 수 있는 조합과 총액을 비교해 드려요."
    >
      <div className="info-cards">
        <InfoCard title="어떻게 찾고 계산하나요">
          <p>
            고른 조건으로 검수한 정책을 찾고, 조건마다 충족 · 미충족 · 확인 필요를 판정해요. 함께 받을 수
            없는 정책은 빼고, 총액이 큰 조합을 최대 3개까지 보여 드려요.
          </p>
          <p>자격 판정, 금액, 조합, 현재가치는 모두 정해진 규칙(코드)으로 계산해요.</p>
        </InfoCard>

        <InfoCard title="AI는 어디에 쓰나요">
          <ul>
            <li>정책 공고 원문에서 조건 · 금액 · 함께 받을 수 없는 규칙을 찾아요. 사람이 검수한 뒤에만 반영해요.</li>
            <li>결과 화면의 맞춤 설명 문장을 써요. AI가 쓴 문장에는 [AI] 표시를 붙여요.</li>
            <li>금액과 자격 같은 숫자 계산은 AI가 하지 않아요.</li>
          </ul>
        </InfoCard>

        <InfoCard title="데이터 출처와 갱신">
          <p>
            온통청년 · 보조금24 등 공식 자료를 써요. 매일 새벽 3시에 새로 확인하고, 정책마다 출처와 마지막
            확인일을 함께 보여 드려요.
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
          <p>고른 조건은 분석에만 쓰고, 분석이 끝나면 바로 폐기해요.</p>
          <a className="ui-button ui-button--secondary" href={paths.privacy}>
            개인정보 처리방침 보기
          </a>
        </InfoCard>
      </div>
    </InfoLayout>
  );
}
