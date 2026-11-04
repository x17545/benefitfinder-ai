# 프론트엔드

작업 전에는 루트 지침과 이 경로의 [추가 에이전트 지침](AGENTS.md)을 함께 읽는다.

김하정이 UI 디자인·React 화면·백엔드 API 연결을 맡는다. 이현서는 요구사항·사용자 흐름·화면 구조·프로토타입을 설계하고, 양승현은 연결할 API·SSE를 제공한다. 상세 담당·인계는 [기능 명세 9장](../../docs/product/REQUIREMENTS.md#9-팀원별-담당-기능-및-협업-기준)을 따른다. 다른 영역 기여는 협업 규칙에 따라 조율한다.

화면·문구·동작의 원본은 [기획 명세 v2](../../docs/product/REQUIREMENTS.md)다. 선택지 13단계, 진행 상태, 조합·현재가치·상황 변화, 상세·빈 결과·오류, 기억하기·이미지 저장·이용 통계를 구현 대상으로 한다. [HTML 시안](../../docs/prototype/index.html)과 설계 이미지는 가상 데이터를 쓰는 화면 자료이며 제품 코드는 React로 작성한다.

## 확정 스택

- React + TypeScript strict + Vite를 사용하는 SPA로 확정했다.
- 패키지 관리자는 pnpm이다. 루트에서 pnpm 11.25.0을 pin하고 Node 24를 기준으로 한다. 웹 패키지 이름은 `@benefitfinder/web`이다. lockfile 생성과 의존성 설치는 루트에서 수행한다.
- 백엔드는 FastAPI + PostgreSQL로 확정했다. 프론트엔드는 DB에 직접 접근하지 않고 API 경유로만 데이터를 받는다.

## 실제 디렉터리 구조

```text
apps/web/
  README.md
  AGENTS.md
  .env.example               개발 서버 전용 예시 (브라우저 공개값 없음)
  package.json               name @benefitfinder/web, 스크립트 dev/lint/typecheck/build/preview/test
  vite.config.ts             dev 127.0.0.1:5173 strictPort, /api/v1·/health 프록시
  vitest 설정                vite.config.ts의 test 필드 (tests/**/*.test.ts)
  tsconfig.json              tsc -b 참조 (app + node)
  eslint.config.js           ESLint flat config (TS/React hooks)
  index.html                 SPA 진입 HTML
  public/                    정적 자산 자리
  src/
    main.tsx                 앱 진입점
    vite-env.d.ts
    app/                     앱 시작과 라우팅과 공통 화면 구성
      App.tsx                S1·S2·L·S3 라우팅과 공통 화면 조립
      router.ts              history 기반 이동·뒤로 가기
      providers/             필요한 React Context·앱 공용 provider 조립 (자리)
      styles/                전역 스타일
    features/
      benefits/              분석 요청·진행, 조합/목록/빈 결과, 이미지 저장
      profile/               13단계 입력, 탭 내 복원, 30일 기억하기·삭제
    shared/
      api/                   health·지역 선택지·추천 JSON/SSE·정책 상세 클라이언트
      api/generated/         JSON Schema에서 생성한 타입 — 직접 수정 금지
      ui/                    버튼·선택지·상단 바·하단 메뉴·상태·토스트
      config/                공개 클라이언트 설정 (api.ts: 경로·문서 URL)
      types/                 UI 전용 타입 자리
    domains/youth/            청년정책 선택지·조건 표시 설정
  tests/                     API·입력/저장·조합 표시·이미지 날짜 검증
```

불필요한 범용 레이어는 추가하지 않는다. 새 디렉터리는 실제 재사용이 확인될 때 만든다.

## 실행 (저장소 루트 기준)

```sh
pnpm dev:web                                    # 웹 개발 서버 시작
pnpm --filter @benefitfinder/web dev            # 같은 서버를 패키지 지정으로 시작
pnpm --filter @benefitfinder/web lint           # ESLint
pnpm --filter @benefitfinder/web typecheck      # tsc -b
pnpm --filter @benefitfinder/web build          # tsc -b && vite build
pnpm --filter @benefitfinder/web preview        # 빌드 결과 미리보기
pnpm --filter @benefitfinder/web test           # vitest run
```

첫 설치는 루트에서 `pnpm install --frozen-lockfile`로 한다. 계약 타입은 이미 포함돼 있다. 계약 변경 후 `pnpm types:generate`를 실행하고 결과도 커밋한다. `pnpm check:web`로 전체 웹 검사를 실행한다. 설치·의존성 추가는 [시작 안내](../../docs/development/GETTING_STARTED.md)를 따른다.

## 실행 주소와 현재 화면

- 웹 개발 서버: `http://127.0.0.1:5173` (strictPort, 고정)
- 백엔드: `http://127.0.0.1:8000`, 생존 확인 `GET /health`
- 시작 화면에서 필수 동의 후 13단계 조건을 고르고 분석을 요청한다. 결과 화면은 서버가 준 조합·금액·기간별 현재가치 결과를 표시하며, 대안을 고르면 해당 조합의 포기한 정책과 근거를 연결한다. 선택한 기간에 결과가 없어도 다른 기간에 결과가 있으면 탭을 유지한다. 계산할 조합이 없어도 검수 대기·모집 종료·조건 미충족 목록과 계산 불가 안내는 유지하며, 6개 분기에 이들까지 모두 없을 때만 전체 빈 결과 화면을 표시한다.
- S4 상황 변화·S5 정책 상세·이용 안내·개인정보 페이지는 자리표시자다. 실제 추천·지역·정책 상세 백엔드 API도 아직 없다.
- 개발 모드에서만 지역 API 실패 시 `[가상]` 지역을, 분석 실패 후 [예제 결과로 보기]를 제공한다. 가상 결과에는 개발용 표시를 붙인다. 운영 빌드에서는 API 오류를 그대로 표시하며 예제 진입 버튼을 제공하지 않는다.

## 연결

- 개발 프록시는 `/api/v1`과 `/health`를 FastAPI로 넘긴다. 필요하면 `apps/web/.env.example`을 같은 폴더의 `.env`로 복사해 변경한다. 대상은 `API_PROXY_TARGET` (기본값 `http://127.0.0.1:8000`)이며 `VITE_` 접두사가 없으므로 브라우저에 공개되지 않는다.
- `/docs`와 `/openapi.json`은 프록시하지 않는다. API 문서는 `http://127.0.0.1:8000/docs`로 직접 연다.
- 운영은 same-origin 리버스 프록시를 권장하며 선택된 배포 주소를 사용한다.
- React 앱 자체는 정적 SPA 출력이며 서버가 아니다.
- 분석 POST는 SSE와 JSON 응답을 처리한다. `fetch` 스트림의 UTF-8 문자와 CRLF가 청크 사이에 나뉘어도 진행·최종 결과·오류를 해석하고 취소·60초 제한을 적용한다. 사용자 조건을 GET URL에 싣지 않는다.
- 추천 응답은 `1y`·`2y`·`all`만 받아들이며 6개 분기 안의 조합·정책·자격 판정·근거·부가 목록을 검증한 뒤 사용한다. 잘못된 응답은 분석 실패로 처리하고, 같은 검증에서 실패한 결과 캐시는 삭제한다.
- 정책 상세도 금액 설명·모집 정보·중복 제한·출처·근거의 중첩 구조를 검증한다. 실패한 상세는 화면 정보와 공유 캐시에 남기지 않고 기본 정책 이름으로 표시하며, 다음 화면 진입 때 다시 받는다.
- 입력 중인 값·동의·기억하기 선택은 sessionStorage `ypm.flow.v1`, 마지막 결과는 `ypm.lastResult`로 탭 안에서만 보관한다. 새로고침·뒤로 가기 때 복원하고 입력값이 실제로 바뀌면 이전 결과와 진행 요청을 무효화한다. 저장소가 차단되면 메모리로만 동작한다.
- 30일 기억하기는 선택한 경우 분석 시작 순간 localStorage `ypm.conditions.v1`에 저장한다. 시작 화면 재진입·새로고침 때 현재 탭의 선택을 유지하며, 지난 조건 카드가 있다는 이유만으로 기억하기를 켜지 않는다. [이 조건으로 다시 보기]는 저장된 조건을 재사용하고 기억하기를 켠다. [내 정보 지우기]는 저장값과 탭 안 입력·동의·결과를 모두 지운다. 민감한 조건 값은 GA에 보내지 않는다.

## 계약 타입

- 계약 요청·응답 타입은 수기로 중복 정의하지 않는다. `pnpm types:generate`로 `contracts/v1/*.schema.json`에서 파일별로 생성한다.
- health 타입은 `src/shared/api/generated/health.ts`에서 가져온다: `import type { HealthResponse } from './generated/health.js'`. `generated/` 폴더는 직접 수정하지 않는다.
- 화면에서 수집 원본이나 DB 구조를 직접 사용하지 않는다. `contracts/v1`을 기준으로 서버와 연결한다.
- 자격 판정과 수혜액 계산은 서버 결과를 그대로 표시한다. 같은 정책 규칙을 프론트엔드에서 다시 구현하지 않는다.
- 청년정책에만 필요한 입력과 문구는 `domains/youth`에 둔다. 공통 혜택 카드와 근거 표시는 재사용한다.
- 페이지별 파일은 관련 `features`에 두고, 실제 여러 기능에서 재사용할 때 `shared`로 이동한다.

## 개발 자료

- API가 준비되기 전에는 `contracts/fixtures`의 가상 요청과 응답으로 개발한다. 테스트와 개발 시에만 가상 데이터를 사용하고 기본 실행에서 가상 정책을 실제처럼 제공하지 않는다.
- 입력과 로딩과 빈 결과와 오류와 추가 확인 필요 상태를 함께 구현하고 키보드 접근과 입력 레이블을 확인한다.

## 실행과 검증

- `lint`는 ESLint flat config (TS/React hooks)로 실행한다. `eslint-disable`이나 `any` 우회로 오류를 숨기지 않는다.
- `typecheck`와 `build`는 `tsc -b`를 사용한다.
- `test`는 `vitest run`이며 추천·정책 상세의 중첩 응답과 기간 키·SSE 청크 경계, 요청 변환, 입력/결과 복원·무효화·삭제, 기억하기 선택 유지, 기간별 빈 결과·계산 불가·보완 목록, 선택 조합의 제외 목록과 상세 재시도를 검증한다.
- 기능과 회귀 테스트는 필요해졌을 때 추가한다. 문서 변경에는 더미 테스트를 쓰지 않는다.

상세 설계는 [아키텍처 문서](../../docs/architecture/ARCHITECTURE.md)를 참고한다.

추가 회의에 따라 초기 MVP의 관심 선택·결과·상세는 취업·주거 두 분류만 노출한다. 설계 PNG는 이전 범위를 포함한 화면 이력이며 현재 문구와 가상 금액은 [현행 기능 명세](../../docs/product/REQUIREMENTS.md)를 따른다. 클릭 시안도 참고 자료이며 명세와 다르면 명세를 기준으로 맞춘다.
