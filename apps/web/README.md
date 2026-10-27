# 프론트엔드

작업 전에는 루트 지침과 이 경로의 [추가 에이전트 지침](AGENTS.md)을 함께 읽는다.

주 담당은 기획·프론트엔드다. 다른 영역 기여는 협업 규칙에 따라 조율한다.

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
      App.tsx                개발 시작 화면 + 백엔드 연결 상태
      providers/             필요한 React Context·앱 공용 provider 조립 (자리)
      styles/                전역 스타일
    features/
      benefits/              혜택 결과와 상세와 조건 확인 (자리)
      profile/               사용자 조건 입력과 수정 (자리)
    shared/
      api/                   API 클라이언트 (health.ts)
      api/generated/         JSON Schema에서 생성한 타입 — 직접 수정 금지
      ui/                    여러 화면이 재사용하는 UI (자리)
      config/                공개 클라이언트 설정 (api.ts: 경로·문서 URL)
      types/                 UI 전용 타입 자리
    domains/youth/            청년정책 전용 입력과 표시 설정 (자리)
  tests/                     프론트엔드 테스트 (health client 검증)
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

## 실행 주소와 개발 시작 화면

- 웹 개발 서버: `http://127.0.0.1:5173` (strictPort, 고정)
- 백엔드: `http://127.0.0.1:8000`, 생존 확인 `GET /health`
- 시작 화면은 개발용이다. 백엔드 연결 상태(확인 중·연결됨·연결 실패)와 다시 시도 버튼, 백엔드 API 문서 링크를 보여준다.
- 제품 기능(정책 조회·추천 등)은 아직 구현되지 않았다. 가상 정책을 실제처럼 표시하지 않는다.
- 백엔드 없이도 웹은 실행된다. 이 경우 연결 안내만 실패로 표시된다.

## 연결

- 개발 프록시는 `/api/v1`과 `/health`를 FastAPI로 넘긴다. 필요하면 `apps/web/.env.example`을 같은 폴더의 `.env`로 복사해 변경한다. 대상은 `API_PROXY_TARGET` (기본값 `http://127.0.0.1:8000`)이며 `VITE_` 접두사가 없으므로 브라우저에 공개되지 않는다.
- `/docs`와 `/openapi.json`은 프록시하지 않는다. API 문서는 `http://127.0.0.1:8000/docs`로 직접 연다.
- 운영은 same-origin 리버스 프록시를 권장하며 선택된 배포 주소를 사용한다.
- React 앱 자체는 정적 SPA 출력이며 서버가 아니다.
- 분석 POST의 응답은 SSE다. `fetch` 스트림에서 청크를 누적해 이벤트 단위로 해석하고 진행·최종 결과·오류·취소를 처리한다. 사용자 조건을 GET URL에 싣지 않는다. (제품 기능 구현 시 적용)
- 서버 무저장 원칙에 따라 기억하기는 동의한 경우 브라우저에 30일 보관한다. 결과용 sessionStorage와 구분하고 삭제·만료·다른 조건 변경 시 캐시 무효화를 함께 구현한다. 민감한 조건 값은 GA에 보내지 않는다.

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
- `test`는 `vitest run`이며 `tests/` 아래 health client의 실패·응답 검증 테스트를 둔다.
- 기능과 회귀 테스트는 필요해졌을 때 추가한다. 문서 변경에는 더미 테스트를 쓰지 않는다.

상세 설계는 [아키텍처 문서](../../docs/architecture/ARCHITECTURE.md)를 참고한다.

추가 회의에 따라 초기 MVP의 관심 선택·결과·상세는 취업·주거 두 분류만 노출한다. 설계 PNG는 이전 범위를 포함한 화면 이력이며 현재 문구와 가상 금액은 [현행 기능 명세](../../docs/product/REQUIREMENTS.md)를 따른다. 클릭 시안도 참고 자료이며 명세와 다르면 명세를 기준으로 맞춘다.
