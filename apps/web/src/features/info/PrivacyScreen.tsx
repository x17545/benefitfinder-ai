import type { JSX } from 'react';
import { Button } from '../../shared/ui/Button.tsx';
import { clearMyData } from '../profile/clearMyData.ts';
import { InfoCard, InfoLayout } from './InfoLayout.tsx';

/** P2 개인정보 처리방침 (기획서 4.8, 7.2 개인정보 보호) */
export function PrivacyScreen(): JSX.Element {
  return (
    <InfoLayout
      title="개인정보 처리방침"
      heading="개인정보 처리방침"
      lead="회원가입 없이 이용해요. 이름 · 연락처 · 주민등록번호는 받지 않아요."
    >
      <p className="info-highlight">
        <svg
          width="18"
          height="18"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <rect x="5" y="11" width="14" height="10" rx="2" />
          <path d="M8 11V8a4 4 0 0 1 8 0v3" />
        </svg>
        고른 조건은 서버에 저장하지 않아요. 이 브라우저의 입력과 결과는 직접 지울 수 있어요.
      </p>

      <div className="info-cards">
        <InfoCard title="받는 정보">
          <p>
            조건 고르기 13단계에서 고른 값만 받아요. 나이, 사는 지역, 지금 상황, 학력, 결혼, 사는 형태와 집
            계약, 소득 · 재산 구간, 해당 조건, 관심 지원, 받았던 지원, 가구 · 부모님 정보(선택)예요.
          </p>
        </InfoCard>

        <InfoCard title="서버로 보내는 정보와 보관">
          <ul>
            <li>[내 조합 찾기]를 누르면 고른 조건을 서버로 보내 자격 판정과 조합 계산에만 써요.</li>
            <li>서버는 선택한 조건을 분석에 사용하고, DB · 파일 · 기록(로그)에 저장하지 않아요.</li>
            <li>오류 응답에도 입력한 조건이나 소득 값, 부모님 정보를 넣지 않아요.</li>
          </ul>
        </InfoCard>

        <InfoCard title="생성형 AI로 보내는 것">
          <ul>
            <li>
              설명은 기본 안내로 제공해요. AI 설명을 켠 환경에서는 정책 원문, 일반 조건의 판정 결과와
              코드가 계산한 지원 금액을 서버에서 실행하는 AI 모델에 전달해요.
            </li>
            <li>
              소득 · 재산, 9단계 해당 조건(기초생활수급 · 차상위 · 한부모 · 장애 등), 부모님 정보는 AI로 보내지
              않아요.
            </li>
          </ul>
        </InfoCard>

        <InfoCard title="이 기기에 저장되는 것">
          <ul>
            <li>
              입력한 조건(소득 · 재산 · 해당 조건 · 부모님 정보 포함)과 동의, 마지막 결과는 새로고침 후
              이어 볼 수 있도록 이 탭에 임시 보관해요. 기억하기를 꺼도 이 탭에는 남아요.
            </li>
            <li>
              &lsquo;이 기기에 내 조건 기억하기&rsquo;를 켜고 분석을 시작하면 조건을 이 브라우저에 추가로
              저장해요. 저장한 지 30일이 지나면 다음 접속 때 지워요.
            </li>
            <li>기억하기를 끄고 다음 분석을 시작하면 이전에 기억한 조건을 지워요.</li>
          </ul>
        </InfoCard>

        <InfoCard title="이용 통계">
          <p>
            현재 구글 애널리틱스 등 이용 통계 도구는 연결하지 않았어요. 이용 통계용 쿠키를 사용하거나
            고른 조건을 통계 도구에 보내지 않아요.
          </p>
        </InfoCard>

        <InfoCard title="지우는 방법">
          <p>
            화면 아래 [내 정보 지우기] 또는 아래 [지금 지우기]를 누르면 이 브라우저에 기억한 조건과 현재
            탭의 입력 · 동의 · 마지막 결과를 바로 지워요.
          </p>
          <Button variant="secondary" onClick={clearMyData}>
            지금 지우기
          </Button>
        </InfoCard>
      </div>
    </InfoLayout>
  );
}
