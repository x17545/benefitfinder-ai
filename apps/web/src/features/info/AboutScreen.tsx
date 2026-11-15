import type { JSX } from 'react';
import { paths } from '../../app/router.ts';
import { InfoCard, InfoLayout } from './InfoLayout.tsx';
import { useServiceMeta } from '../../shared/api/serviceMetaStore.ts';

/** P1 이용 안내 (기획서 4.8의 주제를 현재 구현 범위로 안내) */
export function AboutScreen(): JSX.Element {
  const service = useServiceMeta();
  const virtual = service.status === 'ok' && service.meta.virtual_data === true;
  return (
    <InfoLayout
      title="이용 안내"
      heading="청년 정책 매니저는 이렇게 계산해요"
      lead={virtual
        ? '현재는 가상 정책으로 입력과 계산 흐름을 보여 드려요. 실제 받을 수 있는 지원금이 아니에요.'
        : '고른 조건으로 청년 취업·주거 지원을 비교해요. 최종 자격과 최신 조건은 신청 기관에서 확인해 주세요.'}
    >
      <div className="info-cards">
        <InfoCard title="어떻게 찾고 계산하나요">
          <p>
            고른 조건을 계산에 사용하는 정책의 규칙과 비교해 조건마다 충족 · 미충족 · 확인 필요를 판정해요. 함께 받을 수
            없는 정책은 빼고, 총액이 큰 조합을 최대 3개까지 보여 드려요.
          </p>
          <p>자격 판정, 금액, 조합, 현재가치는 모두 정해진 규칙(코드)으로 계산해요.</p>
        </InfoCard>

        <InfoCard title="AI는 어디에 쓰나요">
          <ul>
            <li>정책 자료를 준비할 때 공고 원문에서 조건 · 금액 · 함께 받을 수 없는 규칙의 후보를 찾고 사람이 검수해요.</li>
            <li>AI 설명을 사용하는 경우에만 [AI] 표시를 붙여요. AI를 사용할 수 없으면 기본 안내를 보여 드려요.</li>
            <li>금액과 자격 같은 숫자 계산은 AI가 하지 않아요.</li>
          </ul>
        </InfoCard>

        <InfoCard title="데이터 출처와 갱신">
          <p>
            지역 선택은 공식 행정표준코드를 사용해요. 현재 자료 상태와 갱신 시각은 화면 아래에서 확인할 수 있어요.
            정책마다 표시된 출처와 확인일을 확인해 주세요. 갱신 시각만으로 정책의 모든 조건이 최신이라고 보장하지 않아요.
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
          <p>고른 조건은 서버에 저장하지 않아요. 입력과 결과의 브라우저 보관 · 삭제 방법을 확인해 주세요.</p>
          <a className="ui-button ui-button--secondary" href={paths.privacy}>
            개인정보 처리방침 보기
          </a>
        </InfoCard>
      </div>
    </InfoLayout>
  );
}
