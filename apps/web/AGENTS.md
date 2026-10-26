# 프론트엔드 추가 지침

- 루트 `AGENTS.md`와 `apps/web/README.md`를 먼저 따른다. 주 책임은 기획 프론트엔드이며, 공용 영역에 영향을 주면 관련 담당과 조율한다.
- 스택은 React + TypeScript strict + Vite SPA, 패키지 관리자 npm으로 확정했다. 임의로 바꾸지 않는다. 큰 스택 변경이 필요하면 팀 합의로 정한다.
- 백엔드 확정은 FastAPI + PostgreSQL이다. 프론트엔드는 DB에 직접 접근하지 않고 `/api/v1` 경유로만 연결한다.
- 기존 구조 `features/benefits`, `features/profile`, `domains/youth`, `shared/ui`, `shared/api`, `app`을 유지한다. `app/providers`, `app/styles`, `shared/config`, `shared/types`, `public`, `tests`는 실제 필요한 최소 자리이다. 불필요한 범용 레이어를 추가하지 않는다.
- TypeScript strict를 사용한다. UI는 `.tsx`, 비UI 로직과 타입은 `.ts`로 둔다.
- `shared/types`는 UI 전용 타입 자리이다. 계약 요청과 응답 타입은 수기로 중복 정의하지 않고 초기화 시 `contracts/v1` JSON Schema에서 생성한다. OpenAPI와 계약 일치는 별도 검증하며 생성 도구는 미정이다.
- `VITE_`는 공개값이다. 비밀값과 토큰과 개인정보를 넣지 않으며 `.env.example`에는 `VITE_API_BASE_URL=/api/v1` 같은 공개 예시만 둔다.
- 개발 프록시는 `/api/v1`과 `/health`를 FastAPI로 넘긴다. 운영은 same-origin 리버스 프록시를 권장하며 선택된 배포 주소를 사용한다. React 앱은 서버 SSR이 아니다.
- 정책 자격과 지원금 계산을 화면에 재구현하지 않는다. 서버의 판정 상태와 근거를 그대로 표시한다.
- `contracts` 예제로 서버 없이 개발할 수 있다. 가상 결과와 실제 결과를 구분하며 기본 실행에서 가상 정책을 실제처럼 제공하지 않는다.
- 입력과 로딩과 빈 결과와 오류와 추가 확인 필요 상태를 함께 구현하고 키보드 접근과 입력 레이블을 확인한다.
- 스택 초기화 시 실제 lint와 typecheck와 build 명령을 README와 공통 검사에 연결한다. 초기화 PR에서 `tsc --noEmit`과 린트와 `vite build`에 해당하는 실제 스크립트를 추가하고 실행 결과를 보고한다. 현재 Python 검사 통과만으로 화면 검증 완료를 보고하지 않는다.
- 기능과 회귀 테스트는 필요해졌을 때 추가한다. 문서 변경에 더미 테스트를 쓰지 않는다.
