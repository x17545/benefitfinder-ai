# 프론트엔드

주 담당은 기획·프론트엔드다. 다른 영역 기여는 협업 규칙에 따라 조율한다.

화면·문구·동작의 원본은 [기획 명세 v2](../../REQUIREMENTS_v2.md)다. 선택지 13단계, 진행 상태, 조합·현재가치·상황 변화, 상세·빈 결과·오류, 기억하기·이미지 저장·이용 통계를 구현 대상으로 한다. [HTML 시안](../../docs/prototype/index.html)과 설계 이미지는 가상 데이터를 쓰는 화면 자료이며 제품 코드는 React로 작성한다.

## 확정 스택

- React + TypeScript strict + Vite를 사용하는 SPA로 확정했다.
- 패키지 관리자는 npm이다. 초기화 PR에서 `package-lock.json`을 추가하고 이후 일관되게 사용한다.
- 백엔드는 FastAPI + PostgreSQL로 확정했다. 프론트엔드는 DB에 직접 접근하지 않고 API 경유로만 데이터를 받는다.
- 현재 `apps/web`에는 `package.json`, lockfile, `node_modules`, `vite.config.ts`, 앱 진입점, `src/vite-env.d.ts`가 없다. 실행·빌드·테스트 스크립트도 없다.

## 실제 디렉터리 구조

`src` 아래 진입점과 화면 코드는 아직 없다. 아래는 현재 저장소에 있는 실제 경로이며, 코드 디렉터리는 빈 자리 표시용 `.gitkeep`만 포함한다.

```text
apps/web/
  README.md
  AGENTS.md
  .env.example               공개 예시값만 둔다
  public/                    정적 자산 자리
  src/
    app/                     앱 시작과 라우팅과 공통 화면 구성
      providers/             필요한 React Context·앱 공용 provider 조립
      styles/                전역 스타일 자리
    features/
      benefits/              혜택 결과와 상세와 조건 확인
      profile/               사용자 조건 입력과 수정
    shared/
      api/                   API 클라이언트
      ui/                    여러 화면이 재사용하는 UI
      config/                공개 클라이언트 설정 자리, 비밀값 금지
      types/                 UI 전용 타입 자리
    domains/youth/            청년정책 전용 입력과 표시 설정
  tests/                     프론트엔드 테스트 자리, 문서 변경용 더미 테스트 금지
```

불필요한 범용 레이어는 추가하지 않는다. 새 디렉터리는 실제 재사용이 확인될 때 만든다.

## 규칙

- TypeScript는 strict를 사용한다. 초기화 시 `tsconfig`에 strict를 켠다.
- UI 컴포넌트는 `.tsx`, UI가 아닌 로직과 타입과 유틸은 `.ts`로 둔다.
- `shared/types`는 UI 전용 타입 자리이다. 계약 요청과 응답 타입은 수기로 중복 정의하지 않고 초기화 시 `contracts/v1` JSON Schema에서 생성한다. OpenAPI와 계약 일치는 별도 검증하며 생성 도구는 미정이다.
- `VITE_` 환경변수는 브라우저에 노출되는 공개값이다. 비밀값과 토큰과 개인정보를 넣지 않는다.
- `.env.example`에는 공개값 예시만 둔다. 현재 예시는 `VITE_API_BASE_URL=/api/v1` 하나다.
- 화면에서 수집 원본이나 DB 구조를 직접 사용하지 않는다. `contracts/v1`을 기준으로 서버와 연결한다.
- 자격 판정과 수혜액 계산은 서버 결과를 그대로 표시한다. 같은 정책 규칙을 프론트엔드에서 다시 구현하지 않는다.
- 청년정책에만 필요한 입력과 문구는 `domains/youth`에 둔다. 공통 혜택 카드와 근거 표시는 재사용한다.
- 페이지별 파일은 관련 `features`에 두고, 실제 여러 기능에서 재사용할 때 `shared`로 이동한다.

## 연결

- 개발 프록시는 `/api/v1`과 `/health`를 FastAPI로 넘긴다. 프록시 설정은 초기화 시점에 `vite.config`와 함께 추가한다.
- 운영은 same-origin 리버스 프록시를 권장하며 선택된 배포 주소를 사용한다.
- React 앱 자체는 정적 SPA 출력이며 서버가 아니다.
- 분석 POST의 응답은 SSE다. `fetch` 스트림에서 청크를 누적해 이벤트 단위로 해석하고 진행·최종 결과·오류·취소를 처리한다. 사용자 조건을 GET URL에 싣지 않는다.
- 서버 무저장 원칙에 따라 기억하기는 동의한 경우 브라우저에 30일 보관한다. 결과용 sessionStorage와 구분하고 삭제·만료·다른 조건 변경 시 캐시 무효화를 함께 구현한다. 민감한 조건 값은 GA에 보내지 않는다.

## 개발 자료

- API가 준비되기 전에는 `contracts/fixtures`의 가상 요청과 응답으로 개발한다. 테스트와 개발 시에만 가상 데이터를 사용하고 기본 실행에서 가상 정책을 실제처럼 제공하지 않는다.
- 입력과 로딩과 빈 결과와 오류와 추가 확인 필요 상태를 함께 구현하고 키보드 접근과 입력 레이블을 확인한다.

## 실행과 검증

실행과 빌드와 테스트 명령은 프레임워크 초기화 후 추가한다. 초기화 시 실제 lint와 typecheck와 build 명령을 README와 공통 검사에 연결한다. 현재 Python 검사 통과만으로 화면 검증 완료를 보고하지 않는다.

기능과 회귀 테스트는 필요해졌을 때 추가한다. 문서 변경에는 더미 테스트를 쓰지 않는다.

상세 설계는 [아키텍처 문서](../../docs/ARCHITECTURE.md)를 참고한다.
