# BenefitFinder AI

청년정책과 혜택을 사용자 상황에 맞춰 찾고, 선택과 신청 준비를 돕는 AI 서비스입니다.

첫 서비스는 **청년정책·청년 대상 혜택**에 집중합니다. 만 19~39세를 중심으로 13단계 선택지에서 조건을 확인하고, 자격 진단·지원금 조합·현재가치·상황 변화·신청 안내를 제공합니다. 다른 대상과 분야의 혜택은 scale up 단계에서 추가합니다.

## 현재 상태

기획 및 초기 설계 단계입니다. 아키텍처, 담당 영역별 디렉터리, 공용 데이터 계약·가상 예제, 에이전트 지침과 공통 검사·CI 설정이 있습니다. 웹 화면, API 서버, AI 실행, 데이터 수집은 아직 구현하지 않았습니다.

2026-10-27 합의로 **React + TypeScript + Vite, Python + FastAPI, PostgreSQL**을 사용합니다. 스택 선택은 완료했으며, 실행 프로젝트 초기화와 제품 기능 구현은 다음 단계입니다.

기능·화면·사용자 흐름의 원본은 기획자가 작성하고 사용자가 승인한 [기능 요구 명세 v2](REQUIREMENTS_v2.md)입니다. F01~F11 전체가 개발 대상이며, 자연어 입력·챗봇 대신 선택지 입력을 사용합니다. 전국 정책 수집과 검수 완료 정책 30개부터 시작하는 계획, 브라우저 기억하기·결과 이미지 저장·이용 통계·자동 갱신도 포함합니다. 기술·모듈 경계는 [아키텍처](docs/ARCHITECTURE.md), 교환 형식은 [공용 계약](contracts/README.md), 작업 절차는 [협업 규칙](docs/CONTRIBUTING.md)을 따릅니다. LLM·벡터 검색·배포 제품 등 구현 도구는 아직 선택하지 않았습니다.

필드 이름·자료형과 11.2의 임시 기본값은 기획자가 다음 회의에서 확인할 초안입니다. 이번 계약 정리는 현재 개발을 위한 기준이며, 팀의 최종 합의를 대신하지 않습니다.

## 확정 기술 스택

| 영역 | 기술 | 적용 원칙 |
|---|---|---|
| 프론트엔드 | React + TypeScript + Vite | 브라우저에서 실행하는 SPA. TypeScript strict 모드 사용 |
| 백엔드 | Python + FastAPI | 요청·응답 검증, 사용 사례 호출, API 제공 |
| 데이터베이스 | PostgreSQL | 정책·조건·출처·갱신 이력을 저장하는 기본 DB |
| AI·데이터·자동화 | 같은 백엔드의 Python 모듈 | 모델·판정 코드를 공유하고 수집 작업은 별도 명령으로 실행 |

초기화 시 프론트엔드는 npm과 `package-lock.json`을 사용합니다. ORM·DB 드라이버·마이그레이션 도구, LLM SDK, 벡터 검색 도구, 배포 서비스는 아직 선택하지 않았습니다. pgvector는 필요성이 확인된 뒤 추가할 수 있는 선택 사항입니다.

## 아키텍처

한 저장소에서 Vite 기반 React 프론트엔드와 FastAPI 백엔드를 관리합니다. 프론트엔드는 HTTP API로만 서버에 접근하고 PostgreSQL이나 LLM의 비밀 키를 직접 사용하지 않습니다. 백엔드는 API·서비스 흐름·분야 규칙·AI·수집·외부 연동을 모듈로 나누고, 초기에는 하나의 애플리케이션으로 통합합니다. 수집 작업은 같은 코드베이스의 별도 실행 진입점으로 운영하도록 설계합니다.

청년정책에만 해당하는 조건은 `domains/youth`에 두고, 혜택·근거·자격 확인 결과 같은 공통 개념은 다른 분야에서도 재사용합니다.

```text
apps/web/                         React + TypeScript + Vite SPA의 구조
  public/                        가공 없이 제공할 공개 정적 파일
  src/app/                       앱 조립·라우팅·providers·스타일
  src/features/                  혜택 조회·사용자 조건 등 기능
  src/shared/                    UI·API·공개 설정·공용 타입
  src/domains/youth/              청년정책 전용 입력·표시
  tests/                         향후 프론트엔드 테스트
backend/src/benefitfinder/
  api/                            FastAPI의 v1 라우터·요청/응답 스키마·의존성
  application/                    사용 사례 조율·공통 인터페이스
  domain/                         공통 혜택 모델·판정 규칙
  domains/youth/                  청년정책 고유 규칙
  ai/                             Agent·RAG·설명 생성
  ingestion/                      데이터 수집·정제
  infrastructure/                LLM·검색 등 외부 연동
    database/                    PostgreSQL 연결·모델·저장소 구현
  jobs/                           수집·갱신 실행 진입점
backend/migrations/versions/      향후 PostgreSQL 스키마 변경 이력
backend/tests/                    향후 백엔드 기능 테스트
contracts/                        공용 JSON Schema·가상 예제
data/                             로컬 데이터 취급 안내
docs/                             기획·설계·협업 문서
tests/                            저장소 공용 계약 검증
scripts/                          로컬·CI 공통 검사
.github/                          PR 템플릿·CI 설정
```

## 팀 구성과 주 작업 영역

| 이름 | 역할 | 주 작업 영역 |
|---|---|---|
| 양승현 | 조장, PM | `docs/`, 범위·일정·통합 조율 |
| 이현서 | 기획, 프론트엔드 | `apps/web/`, 화면·사용자 흐름 |
| 김하정 | 백엔드, 자동화 | `api/`, `application/`, `infrastructure/`, `jobs/`, `backend/migrations/` |
| 탁서윤 | AI, Agent, RAG | `ai/`, AI 평가 |
| 윤정찬 | 데이터 수집, 정제 | `ingestion/`, 데이터 품질 검증 |

`contracts/`, `domain/`, `domains/youth/`는 관련 담당자가 함께 검토합니다. 역할은 주 책임을 뜻하며, 다른 영역의 기여도 가능합니다.

## 개발 시작

각 담당자는 [공용 계약과 가상 예제](contracts/README.md)를 기준으로 독립 개발을 시작할 수 있습니다. 예제는 실제 정책이나 사용자의 데이터가 아닙니다.

환경 변수·키·DB 덤프·수집 데이터·빌드 결과물은 [.gitignore](.gitignore)에서 제외합니다. `.env.example`, npm lockfile, 마이그레이션과 가상 예제는 공유합니다. 제외 파일을 강제로 추가하면 공통 검사가 실패합니다. 상세 범위와 한계는 [저장소 운영 설정](docs/REPOSITORY_SETTINGS.md)을 참고합니다.

현재 공통 검증에는 Python 3.11 이상만 필요하며, 아래 의존성은 계약 검증용입니다. React·FastAPI·PostgreSQL 실행 환경을 설치하는 명령은 아닙니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python scripts/check.py
```

현재 `package.json`, Vite 설정, FastAPI 실행 진입점, DB 연결·마이그레이션 실행 설정은 없습니다. 실제 초기화 시 버전·의존성·실행 명령을 [프론트엔드 안내](apps/web/README.md)와 [백엔드 안내](backend/README.md)에 추가하고, 웹 lint·typecheck·build와 필요한 기능 검증을 공통 검사·CI에 연결합니다.

코딩 에이전트는 작업 전에 [AGENTS.md](AGENTS.md)와 수정 대상 경로의 추가 지침을 읽습니다. 브랜치는 `feat/`, `fix/`, `docs/`, `refactor/`, `test/`, `chore/`, `ci/` 접두어를 사용하며 `codex/`와 main 직접 작업을 금지합니다. PR 전에는 작업 브랜치에서 최신 `origin/main`을 머지·검증하고 `python scripts/check.py --pr`을 실행합니다. 상태별 명령은 [Git 작업 절차](docs/GIT_WORKFLOW.md)를 따릅니다.

현재 검사는 Git·파일·직접 import·계약·기획용 가상 계산 정답·Python 테스트를 다룹니다. 프론트엔드 실행·실제 API/LLM 통합·정책 사실 검증은 아직 포함하지 않습니다. 계정 권한 제한에 따라 원격 main 보호 설정은 이번 범위에서 제외했습니다. 브랜치·PR·리뷰 절차와 CI를 사용하며, 관리자용 설정은 [저장소 운영 설정](docs/REPOSITORY_SETTINGS.md)에 참고 자료로 남깁니다.

## 문서

- [기능·화면·디자인·계산 규칙의 원본](REQUIREMENTS_v2.md)
- [아키텍처와 확장 방법](docs/ARCHITECTURE.md)
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
