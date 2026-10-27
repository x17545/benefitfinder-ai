# BenefitFinder AI

사용자가 주거지·소득·연령 등을 입력하면, **중복 수급 제한을 반영한 지원금 조합을 총액이 큰 순서로 비교**하는 서비스입니다.

초기 MVP는 **만 19~39세 청년 대상 취업·주거 혜택**에 집중합니다(2026-10-27 추가 회의 결정). 13단계 선택지에서 조건을 확인하고 자격 진단·지원금 조합·현재가치·상황 변화·신청 안내를 제공합니다. 생활·자산형성·교육·장학 등 나머지 청년 혜택과 다른 대상의 혜택은 scale up 단계에서 추가합니다.

공식 API에서 확보하지 못한 중복 수급 제한·예외 조건은 공고·사업 지침·FAQ를 함께 분석해 보완합니다. **데이터 분석·정제 단계에 AI Agent를 활용하는 방향**이며, 탁서윤의 역할 이동도 이 작업에 집중하기 위한 것입니다(2026-10-28 사용자 설명 반영). 검수된 규칙을 사용하는 정책 검색·자격 판정·조합 계산은 일반 백엔드 코드로 구현하고, 추천 이유는 판정 결과와 근거를 템플릿으로 정리합니다. 구체 모델과 Agent 도구는 아직 선정하지 않았습니다.

## 현재 상태

React 개발 시작 화면과 FastAPI 서버를 실행할 수 있습니다. 화면에서 `/health` 연결 상태를 확인하고 `/docs`에서 API 문서를 볼 수 있습니다. 공용 계약·가상 예제·타입 생성·공통 검사·CI를 갖췄습니다. 정책 조회·조합 계산·데이터 수집·DB 연결은 아직 구현하지 않았습니다.

2026-10-27 합의로 **React + TypeScript + Vite, Python + FastAPI, PostgreSQL**을 사용합니다. 실행 프로젝트를 초기화했으며 제품 기능 구현은 다음 단계입니다.

기능·화면·사용자 흐름의 원본은 최신 회의 결정을 반영한 [기능 요구 명세 v2](REQUIREMENTS_v2.md)입니다. F01~F11 전체를 취업·주거 분야에 적용하며, 자연어 입력·챗봇 대신 선택지 입력을 사용합니다. F02는 정책 검색, F03은 분석 흐름·진행 상태를 담당합니다. 전국의 청년 취업·주거 정책을 수집하고 두 분야 안에서 30개 검수를 목표로 하는 계획, 브라우저 기억하기·결과 이미지 저장·이용 통계·자동 갱신도 포함합니다. 기술·모듈 경계는 [아키텍처](docs/ARCHITECTURE.md), 교환 형식은 [공용 계약](contracts/README.md), 작업 절차는 [협업 규칙](docs/CONTRIBUTING.md)을 따릅니다. 배포 제품 등 구현 도구는 아직 선택하지 않았습니다.

필드 이름·자료형과 11.2의 임시 기본값은 기획자가 다음 회의에서 확인할 초안입니다. 이번 계약 정리는 현재 개발을 위한 기준이며, 팀의 최종 합의를 대신하지 않습니다.

## 확정 기술 스택

| 영역 | 기술 | 적용 원칙 |
|---|---|---|
| 프론트엔드 | React + TypeScript + Vite | 브라우저에서 실행하는 SPA. TypeScript strict 모드 사용 |
| 백엔드 | Python + FastAPI | 요청·응답 검증, 사용 사례 호출, API 제공 |
| 데이터베이스 | PostgreSQL | 정책·조건·출처·갱신 이력을 저장하는 기본 DB |
| 데이터·AI·자동화 | 같은 백엔드의 Python 모듈 | Agent로 원문 분석·규칙 후보 추출 → 검수·게시. 사용자 요청의 판정·계산은 코드로 실행 |

패키지 관리는 **Python은 uv, 웹은 pnpm**으로 통일합니다. Python 3.12·Node.js 24·pnpm 11.25.0을 사용하고, `uv.lock`·`pnpm-lock.yaml`을 공유합니다. ORM·DB 드라이버·마이그레이션 도구, 배포 서비스는 아직 선택하지 않았습니다. 데이터 Agent의 LLM·SDK·평가 기준은 비교 후 정하며, RAG·임베딩·벡터 검색은 원문 탐색에 필요할 때 선택합니다.

## 아키텍처

한 저장소에서 Vite 기반 React 프론트엔드와 FastAPI 백엔드를 관리합니다. 프론트엔드는 HTTP API로만 서버에 접근하고 PostgreSQL 비밀 키를 직접 사용하지 않습니다. 백엔드는 API·서비스 흐름·분야 규칙·분석 흐름·수집·외부 연동을 모듈로 나누고, 초기에는 하나의 애플리케이션으로 통합합니다. 수집 작업은 같은 코드베이스의 별도 실행 진입점으로 운영하도록 설계합니다. F02 정책 검색은 구조화 DB 조건으로, F03 분석 흐름·진행 상태는 일반 application 코드로 구현한다.

청년정책에만 해당하는 조건은 `domains/youth`에 두고, 혜택·근거·자격 확인 결과 같은 공통 개념은 다른 분야에서도 재사용합니다.

데이터 준비는 **공식 API·공고 수집 → Agent의 중복 제한·예외 규칙 후보 추출 → 검증·사람 검수 → 버전별 게시**로 설계합니다. 사용자 요청은 게시된 데이터에서 후보를 조회하고 적용되는 제약을 비교한 뒤 조합을 계산합니다. API에 제한 정보가 없다는 이유로 동시 수급을 허용하지 않습니다. 상세는 [정책 데이터 분석·정제 설계](docs/POLICY_DATA_PIPELINE.md)를 따릅니다.

```text
pyproject.toml · uv.lock           Python 의존성·고정 버전 (루트 uv 프로젝트)
package.json · pnpm-lock.yaml      공용 실행 명령·웹 의존성 고정 버전
pnpm-workspace.yaml               웹 패키지와 pnpm 설정
apps/web/                         React + TypeScript + Vite SPA
  public/                        가공 없이 제공할 공개 정적 파일
  src/app/                       앱 조립·라우팅·providers·스타일
  src/features/                  혜택 조회·사용자 조건 등 기능
  src/shared/                    UI·API·공개 설정·공용 타입
  src/domains/youth/              청년정책 전용 입력·표시
  tests/                         API 클라이언트 테스트
backend/src/benefitfinder/
  api/                            FastAPI의 v1 라우터·요청/응답 스키마·의존성
  application/                    사용 사례 조율·공통 인터페이스
  domain/                         공통 혜택 모델·판정 규칙
  domains/youth/                  청년정책 고유 규칙
  ai/                             정책 원문 분석 Agent·규칙 후보 추출·평가
  ingestion/                      데이터 수집·정제
  infrastructure/                검색 등 외부 연동
    database/                    PostgreSQL 연결·모델·저장소 구현
  jobs/                           수집·갱신 실행 진입점
backend/migrations/versions/      향후 PostgreSQL 스키마 변경 이력
backend/tests/                    HTTP·오류 계약 테스트
contracts/                        공용 JSON Schema·가상 예제
data/                             로컬 데이터 취급 안내
docs/                             기획·설계·협업 문서
tests/                            저장소 공용 계약 검증
scripts/                          로컬·CI 공통 검사
.github/                          PR 템플릿·CI 설정
```

## 팀 구성과 주 작업 영역

2026-10-28 역할 변경을 반영했습니다.

| 이름 | 역할 | 주 작업 영역 |
|---|---|---|
| 양승현 | 조장, PM, 자동화 | `docs/`, 범위·일정·통합 조율, `jobs/` 실행·스케줄·재시도·실패 알림 |
| 이현서 | 기획, 프론트엔드 | `apps/web/`, 화면·사용자 흐름 |
| 김하정 | 백엔드 | `api/`, `application/`, `infrastructure/`, `backend/migrations/`, 자동화는 API·DB 연동 범위에서 협업 |
| 탁서윤 | 수집 데이터 분석·정제 | `ingestion/` 분석·정제·검수, `ai/` 원문 분석 Agent·규칙 후보 추출·평가 (윤정찬과 파일별 분담) |
| 윤정찬 | 데이터 수집, 정제 | `ingestion/`의 원천 수집·출처/원본 관리 주축, 정제 공유 |

`contracts/`, `domain/`, `domains/youth/`는 관련 담당자가 함께 검토합니다. 역할은 주 책임을 뜻하며, 다른 영역의 기여도 가능합니다. 데이터 업무는 윤정찬과 탁서윤이 공유하며, Issue에서 파일별 작업을 나눠 같은 파일 동시 수정을 피합니다. 탁서윤은 데이터 Agent를 통한 분석·정제와 품질 평가를 맡고, 김하정은 검수된 규칙을 계산 코드에 연결합니다.

## 개발 시작

각 담당자는 [공용 계약과 가상 예제](contracts/README.md)를 기준으로 독립 개발을 시작할 수 있습니다. 예제는 실제 정책이나 사용자의 데이터가 아닙니다.

먼저 **Node.js 24, uv, pnpm 11.25.0**을 설치합니다. 설치 방법과 용어는 [처음 개발 시작하기](docs/GETTING_STARTED.md)에 정리했습니다. 저장소 루트에서 실행합니다.

```bash
uv sync --locked
pnpm install --frozen-lockfile
pnpm dev
```

- 웹: http://127.0.0.1:5173
- API 문서: http://127.0.0.1:8000/docs
- 서버 상태: http://127.0.0.1:8000/health

`pnpm dev`가 두 서버를 함께 실행하며 `Ctrl+C`로 종료합니다. 따로 실행하려면 `pnpm dev:web` / `pnpm dev:api`를 각 터미널에서 실행합니다. 기본 실행에는 `.env`·PostgreSQL·AI 키가 필요 없습니다. `/health` 성공은 서버 생존 확인이며 DB 연결 확인이 아닙니다. PostgreSQL은 확정 스택이고 연결 구현은 후속 작업입니다.

```bash
uv run --locked python scripts/check.py   # 저장소·API·웹 전체 검사
pnpm types:generate                      # 계약 변경 후 타입 재생성
```

uv는 Python 가상환경·의존성·실행을, pnpm은 JavaScript 의존성·개발 명령을 관리합니다. `pip install`·`npm install`·`npx`를 사용하거나 다른 lockfile을 추가하지 않습니다. 의존성 추가는 `uv add` / `pnpm --filter @benefitfinder/web add`로 하고 두 종류의 lockfile을 커밋합니다.

환경 변수·키·DB 덤프·수집 데이터·빌드 결과물은 [.gitignore](.gitignore)에서 제외합니다. `.env.example`, 두 lockfile, 마이그레이션과 가상 예제는 공유합니다. 제외 파일 강제 추가와 다른 관리자의 lockfile 유입은 공통 검사에서 실패합니다. 상세는 [저장소 운영 설정](docs/REPOSITORY_SETTINGS.md)을 참고합니다.

코딩 에이전트는 작업 전에 [AGENTS.md](AGENTS.md)와 수정 대상 경로의 추가 지침을 읽습니다. 브랜치는 `feat/`, `fix/`, `docs/`, `refactor/`, `test/`, `chore/`, `ci/` 접두어를 사용하며 `codex/`와 main 직접 작업을 금지합니다. PR 전에는 작업 브랜치에서 최신 `origin/main`을 머지·검증하고 `uv run --locked python scripts/check.py --pr`을 실행합니다. 상태별 명령은 [Git 작업 절차](docs/GIT_WORKFLOW.md)를 따릅니다.

현재 검사는 Git·파일·직접 import·계약·기획용 가상 계산 정답·API HTTP 테스트·Ruff·웹 lint/타입/테스트/빌드·생성 타입 최신 여부를 다룹니다. 실제 PostgreSQL·LLM·정책 사실 검증과 제품 기능 완성을 뜻하지 않습니다. 계정 권한 제한에 따라 원격 main 보호 설정은 이번 범위에서 제외했습니다. 브랜치·PR·리뷰 절차와 CI를 사용하며, 관리자용 설정은 [저장소 운영 설정](docs/REPOSITORY_SETTINGS.md)에 참고 자료로 남깁니다.

## 문서

- [처음 개발 시작하기](docs/GETTING_STARTED.md)
- [기능·화면·디자인·계산 규칙의 원본](REQUIREMENTS_v2.md)
- [아키텍처와 확장 방법](docs/ARCHITECTURE.md)
- [정책 데이터 분석·정제와 Agent 활용](docs/POLICY_DATA_PIPELINE.md)
- [협업 규칙](docs/CONTRIBUTING.md)
- [코딩 컨벤션](docs/CONVENTIONS.md)
- [개발 절차](docs/DEVELOPMENT.md)
- [Git 작업 절차](docs/GIT_WORKFLOW.md)
- [에이전트·저장소 운영 설정](docs/REPOSITORY_SETTINGS.md)
- [데이터 계약](contracts/README.md)
- [프론트엔드 구조](apps/web/README.md)
- [백엔드 구조](backend/README.md)
- [현재 프로젝트 기획서](docs/PROJECT_PLAN.md)
- [요구 명세 안내와 이전 버전 기록](docs/REQUIREMENTS.md)
- [클릭 가능한 화면 시안](docs/prototype/index.html)
- [사용자 테스트 진행안](docs/USER_TEST.md)
