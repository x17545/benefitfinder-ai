# AI 혜택 코치 (BenefitFinder AI)

사용자가 주거지·소득·연령 등 자신의 조건을 선택하면, **함께 받을 수 있는 지원금 조합을 총액순으로 비교**하는 서비스다. 화면 이름은 ‘청년 정책 매니저’다.

초기 MVP는 만 19~39세 청년의 **취업·주거 혜택**을 다룬다. 선택 입력 13단계와 F01~F11을 이 범위에 적용하며, 생활·자산형성·교육·장학과 다른 대상의 혜택은 확장 단계에서 추가한다. 공식 원문의 복잡한 중복 제한은 데이터 Agent로 분석·정제하고 사람의 검수를 거쳐 게시한다. 사용자 요청의 자격 판정과 조합 계산은 검수된 규칙과 코드가 수행한다.

현재 실행 가능한 것은 **React 개발 시작 화면과 FastAPI 생존 확인·공통 오류 처리**다. 정책 추천·조합 계산·PostgreSQL 연동·수집·Agent·자동 갱신은 후속 구현이다. 자세한 구현 상태와 문서별 기준은 [문서 지도](docs/README.md)를 본다.

## 로컬 실행

Node.js 24, pnpm 11.25.0, uv 0.9.5 이상을 준비하고 저장소 루트에서 실행한다. Python 3.12와 루트 `.venv`는 uv가 관리한다. 처음 설치하거나 실행이 안 되면 [처음 개발 시작하기](docs/development/GETTING_STARTED.md)를 따른다.

```sh
uv sync --locked
pnpm install --frozen-lockfile
pnpm dev
```

- 웹: [http://127.0.0.1:5173](http://127.0.0.1:5173)
- API 문서: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- 생존 확인: [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)

DB·LLM 키 없이 실행한다. `pip`·`npm`·`npx` 대신 uv·pnpm을 사용하고, 의존성 선언과 lockfile을 함께 관리한다.

## 개발 시작점

| 하고 싶은 일 | 먼저 볼 문서 |
|---|---|
| 어떤 제품인지 이해하기 | [기획 개요·일정·제출물](docs/product/OVERVIEW.md), [기능 명세](docs/product/REQUIREMENTS.md) |
| 작업을 나누고 PR 올리기 | [협업·개발 절차](CONTRIBUTING.md), [Git 작업 절차](docs/development/GIT_WORKFLOW.md) |
| 구현 위치 정하기 | [아키텍처](docs/architecture/ARCHITECTURE.md), [웹 구조](apps/web/README.md), [백엔드 구조](backend/README.md) |
| API·데이터 연결하기 | [공용 계약](contracts/README.md), [정책 데이터 준비](docs/architecture/POLICY_DATA_PIPELINE.md) |
| 코딩 에이전트로 작업하기 | [공용 지침](AGENTS.md), 수정 경로의 하위 `AGENTS.md` |

역할과 기능별 인계의 기준은 [기능 명세 9장](docs/product/REQUIREMENTS.md#9-팀원별-담당-기능-및-협업-기준)이다. 팀원별 장기 브랜치 대신 기능별 짧은 브랜치와 PR을 사용한다. `main` 직접 작업·푸시와 `codex/` 접두어는 금지한다.

## 저장소 구조

```text
apps/web/       React + TypeScript + Vite
backend/        FastAPI, 공통 판정·분야별 규칙·데이터 준비 경계
contracts/      공용 JSON Schema와 가상 예제
docs/
  README.md     문서 지도와 구현 상태
  product/      기획 개요·기능 명세·사용자 테스트
  architecture/ 기술 설계·정책 데이터 준비
  development/  설치·컨벤션·Git·저장소 운영
  prototype/    화면 참고 HTML·PNG
  archive/      과거 기록 (현행 수락 기준 아님)
scripts/        공통 검사와 계약 타입 생성
tests/          계약·기획용 참조 계산 검증
```

## 검증

```sh
uv run --locked python scripts/check.py
```

PR 전에는 작업 브랜치에 최신 `origin/main`을 머지하고 충돌을 해결한다. 검토할 변경을 커밋한 뒤 `uv run --locked python scripts/check.py --pr`을 실행한다. 이 검사는 웹 빌드와 실제 HTTP 오류 계약을 포함하지만, 미구현 정책 기능이나 실제 PostgreSQL·LLM·정책 사실의 검증을 뜻하지 않는다. 상세 범위와 한계는 [저장소 운영 안내](docs/development/REPOSITORY_SETTINGS.md#3-현재-공통-검사의-범위)를 따른다.
