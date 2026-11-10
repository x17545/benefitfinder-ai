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
        고른 조건은 분석에만 쓰고, 분석이 끝나면 바로 폐기해요.
      </p>

      <div className="info-cards">
        <InfoCard title="받는 정보">
          <p>
            조건 고르기 13단계에서 고른 값만 받아요. 나이, 사는 지역, 지금 상황, 학력, 결혼, 사는 형태와 집
            계약, 소득 · 재산 구간, 해당 조건, 관심 지원, 받았던 지원, 가구 · 부모님 정보(선택)예요.
          </p>
        </InfoCard>

        <InfoCard title="서버로 보내는 정보와 폐기">
          <ul>
            <li>[내 조합 찾기]를 누르면 고른 조건을 서버로 보내 자격 판정과 조합 계산에만 써요.</li>
            <li>서버와 기록(로그)에 조건을 저장하지 않고, 분석이 끝나면 바로 폐기해요.</li>
            <li>기록에는 요청 번호, 걸린 시간, 오류 코드만 남아요.</li>
          </ul>
        </InfoCard>

        <InfoCard title="생성형 AI로 보내는 것">
          <ul>
            <li>맞춤 설명을 쓸 때 조건별 판정 결과와 정책 원문만 보내요.</li>
            <li>
              소득 · 재산, 9단계 해당 조건(기초생활수급 · 차상위 · 한부모 · 장애 등), 부모님 정보는 AI로 보내지
              않아요.
            </li>
          </ul>
        </InfoCard>

        <InfoCard title="이 기기에 저장되는 것">
          <ul>
            <li>
              시작 화면에서 &lsquo;이 기기에 내 조건 기억하기&rsquo;를 켰을 때만 이 브라우저에 30일 동안
              저장해요. 30일이 지나면 다음 접속 때 지워요.
            </li>
            <li>마지막 결과는 이 탭에만 잠시 두고, 탭을 닫으면 사라져요.</li>
          </ul>
        </InfoCard>

        <InfoCard title="이용 통계">
          <p>
            서비스를 고치는 데 쓰려고 구글 애널리틱스(쿠키)로 방문 수 같은 이용 통계를 모아요. 고른 조건은
            보내지 않아요.
          </p>
        </InfoCard>

        <InfoCard title="지우는 방법">
          <p>
            모든 화면 아래 [내 정보 지우기]를 누르면 이 기기에 저장된 조건과 마지막 결과를 바로 지워요.
          </p>
          <Button variant="secondary" onClick={clearMyData}>
            지금 지우기
          </Button>
        </InfoCard>
      </div>
    </InfoLayout>
  );
}
