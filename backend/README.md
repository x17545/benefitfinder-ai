# 백엔드

작업 전에는 루트 지침과 이 경로의 [추가 에이전트 지침](AGENTS.md)을 함께 읽는다.

Python + FastAPI와 PostgreSQL을 사용하는 백엔드 구조다. 생존 확인·공통 오류, 공식 지역 선택 API, 가상 MVP 정책의 추천 API를 제공한다. Python 의존성은 루트 uv 프로젝트에서 관리하고 DB 연결은 아직 구현하지 않았다.

`src/benefitfinder/`의 모듈을 한 애플리케이션으로 통합한다. AI·데이터 수집은 담당 디렉터리를 가지며 각각 별도 서버로 배포할 필요는 없다.

기능은 [기획 명세 v2](../docs/product/REQUIREMENTS.md)의 F01~F11을 따른다. 선택 프로필에서 검색·자격·조합·현재가치·상황 변화·상세를 조율하고, HTTP 경계에서 SSE 진행·결과를 제공하는 것이 목표다. 현재 추천 API는 가상 MVP 정책을 JSON으로 반환하며 실제 정책·PostgreSQL 연결과 스케줄러는 후속 작업이다.

```text
backend/
  .env.example                  DB 연결 설정의 가상 양식
  migrations/
    versions/                   향후 PostgreSQL 스키마 변경 이력
  src/benefitfinder/
    bootstrap.py              조립(create_app)
    main.py                   ASGI 진입점(benefitfinder.main:app)
    api/
      v1/routers/health.py        GET /health(생존 확인 전용)
      schemas/                  공통 오류·생존 확인 응답 Pydantic 모델
      dependencies/             조립된 객체를 HTTP 요청에 연결
    application/                사용 사례·외부 기능 인터페이스
    domain/                     순수 공통 모델·판정·계산
    domains/youth/              청년정책 고유 규칙
    ai/                         공식 공고·지침·FAQ 원문 분석·추출·평가 (상류 데이터 Agent, 미구현)
    ingestion/                  수집·정제·품질 검증
    infrastructure/
      database/
        models/                 PostgreSQL 저장 모델
        repositories/           저장소 인터페이스의 PostgreSQL 구현
    jobs/                       수집·갱신 실행 진입점
  tests/test_api.py               부트스트랩 TestClient 검증
```

`bootstrap.py`·`main.py`에서 서버와 원천 어댑터를 조립한다. `GET /health`·`GET /api/v1/options`·`POST /api/v1/recommendations`가 동작한다. 추천은 가상 정책 계산이며 실제 DB 연동 완료를 뜻하지 않는다.

## 실제 지역 선택 API

`GET /api/v1/options`는 [행정표준코드관리시스템 법정동코드 전체자료](https://www.code.go.kr/stdcode/regCodeL.do)를 백엔드에서 읽는다. 공식 화면의 공개 다운로드 `POST https://www.code.go.kr/etc/codeFullDown.do`에 UTF-8 form `codeseId=법정동코드`를 보내 ZIP 안 CP949 TSV를 변환한다. 별도 인증키나 원문 파일 배치 없이 `pnpm dev`로 실행한다.

- `존재` 행만 사용하고 `폐지` 행은 제외한다. 10자리 법정동코드에서 시도 2자리, 자치 시군구 5자리를 반환한다. 일반구는 상위 시로 선택한다(수원시 `41110`, 장안구 등은 별도 선택하지 않음). 세종은 원천의 `3611000000`을 시도 `36`·유일한 하위 `36110`으로 연결한다.
- 시도 개수·명칭을 코드에 고정하지 않는다. 2026-11-07 확인한 공식 자료는 시도 16개·자치 시군구 230개였다. 명세의 기존 ‘17개’와 차이가 있어 공식 현행 목록을 제공하며, 다음 기획 확인에서 숫자 표현을 정리한다. 계약 형식은 바꾸지 않는다.
- 최초 요청 때 다운로드하며 import·서버 시작 때는 네트워크를 호출하지 않는다. 성공 목록은 프로세스별 24시간 캐시하고 다음 요청에 갱신한다. 동시 요청은 다운로드 한 번을 공유한다. 서버 재시작은 즉시 새 자료를 받는 방법이다.
- HTTP 대기 10초, ZIP 4 MiB·본문 16 MiB 한도를 둔다. 빈 자료·손상·중복 코드·부모 없는 시군구를 거부한다. 실패는 공통 `503 dependency_unavailable`, `Retry-After: 60`이며 원천 상세를 노출하지 않는다. 실패 후 60초 동안 재다운로드를 억제하고 만료된 코드나 가상 지역으로 대체하지 않는다.
- `bands`는 명세 4.3의 기존 구간을 제공한다. 부모 소득·재산의 ‘모름’은 요청에서 `null`이므로 구간 코드 목록에 넣지 않는다. 지역 선택은 실제 코드지만 추천 정책과 금액은 여전히 가상 MVP다. 실제 지역별 정책 조회 검증과 구분한다.

재현: `pnpm dev` → 필수 동의 → 나이 → 지역에서 `경기도`·`수원시` 선택 → 나머지 13단계 입력 → 분석·결과. 세종은 하위가 자동 선택되고 시도 변경 시 하위 선택이 초기화된다. 탭 복원·확인 화면 직접 접근·지난 조건 재사용도 현재 목록으로 코드를 확인한다. 조회 실패·빈 목록은 각각 안내와 재시도를 제공하며 개발 모드도 가상 지역을 대체 표시하지 않는다. 수집 원문·요청·결과·화면 캡처는 Git 제외 `data/local/`에만 둔다.

가상 자료 기반 자동 검증: `uv run --locked python -m unittest backend.tests.test_region_options -v`. 실제 원천 연결은 서버 실행 후 `curl --fail http://127.0.0.1:8000/api/v1/options`로 별도 확인한다. 원천 장애 시 `/health` 성공과 지역 목록 준비 상태는 다를 수 있다.

| 모듈 | 배치할 코드 |
|---|---|
| [api/](src/benefitfinder/api/README.md) | FastAPI 라우트, Pydantic 요청 검증, 공통 응답·오류 변환 |
| [application/](src/benefitfinder/application/README.md) | 사용 사례, 검색·저장·AI 등 필요한 인터페이스 |
| [domain/](src/benefitfinder/domain/README.md) | 공통 업무 모델과 순수 판정·계산 |
| [domains/youth/](src/benefitfinder/domains/youth/README.md) | 청년정책 고유 조건과 규칙 |
| [ai/](src/benefitfinder/ai/README.md) | 공식 문서 원문 분석·추출·평가 (운영 판정 경로는 일반 코드) |
| [ingestion/](src/benefitfinder/ingestion/README.md) | 출처별 수집·정제·검증 |
| [infrastructure/](src/benefitfinder/infrastructure/README.md) | PostgreSQL·검색·LLM SDK 등 구체 어댑터 |
| [jobs/](src/benefitfinder/jobs/README.md) | 수집·갱신 실행과 재실행 제어 |

- 조립 코드는 `src/benefitfinder/bootstrap.py`에 두고, `src/benefitfinder/main.py`에서 `app`을 노출한다.
- DB 연결·트랜잭션·저장 모델·저장소 구현은 [database 안내](src/benefitfinder/infrastructure/database/README.md)를 따른다. ORM·드라이버는 아직 선택하지 않았다.
- PostgreSQL 스키마 변경은 `migrations/versions/`에 기록하며 [마이그레이션 안내](migrations/README.md)를 따른다. 마이그레이션 도구는 아직 선택하지 않았으며 실제 이력·실행 설정은 없다. 원본 데이터와 API 키는 넣지 않는다.
- `.env.example`은 가상 연결 문자열 양식이다. DB 환경 변수 로딩은 DB 연결 구현 시 추가한다. 실제 `.env`·DB 비밀번호는 Git에 올리거나 프론트엔드에 전달하지 않는다.
- 기능 테스트는 `tests/`에 담당 모듈별로 추가한다. 부트스트랩 검증은 `tests/test_api.py`([안내](tests/README.md))에 있고, 공용 계약 검증은 저장소 루트 `tests/`에 있다.
- 각 모듈 README와 [의존성 규칙](../docs/architecture/ARCHITECTURE.md)을 따라 구현한다.
- 실행은 DB·LLM 키 없이 된다. 저장소 루트에서 `uv run --locked uvicorn benefitfinder.main:app --reload --reload-dir backend/src --host 127.0.0.1 --port 8000`으로 실행한다. 런타임 의존성 버전·관리는 루트 uv 프로젝트가 담당하며, `GET /health`는 liveness일 뿐이고 DB-ready를 보장하지 않는다. 설치는 루트 `uv sync --locked`, 두 서버 동시 실행은 `pnpm dev`, API만 실행은 `pnpm dev:api`다. pip로 직접 설치하거나 별도 requirements 파일을 만들지 않는다. 자세한 안내는 [처음 개발 시작하기](../docs/development/GETTING_STARTED.md)를 따른다.
- 가상 저장소를 이용한 단위 검증과 실제 PostgreSQL 연동 검증을 구분한다. 실제 DB 구현은 별도 테스트 DB에서 스키마 적용·읽기·쓰기를 확인한다.

2026-10-29 역할 기준: 양승현은 FastAPI·검색·자격 판정·조합 계산·통합 테스트·배포, 윤정찬은 원천 수집·원본/출처/버전 관리·PostgreSQL 적재, 탁서윤은 ingestion 정제·ai 원문 분석/중복 수혜 규칙 추출·jobs 자동화를 맡는다. 저장 모델·마이그레이션은 조회와 적재의 영향을 승현·정찬이 함께 검토한다. 김하정은 React 화면에서 API·SSE를 연결하고, 이현서는 요구사항·사용자 흐름·화면 설계를 맡는다. 상세 인계는 [기능 명세 9장](../docs/product/REQUIREMENTS.md#9-팀원별-담당-기능-및-협업-기준)을 따른다.

제품의 핵심은 중복 수급 제한을 반영한 지원금 조합의 총액순 비교다. 데이터 Agent로 원문 규칙 후보를 추출하고 검증·사람 검수 후 게시한다. 사용자 검색·계산·템플릿 설명은 검수 데이터를 쓰는 일반 코드로 수행한다. 현재 [ingestion](src/benefitfinder/ingestion/README.md)에 목록 수집과 규칙 후보의 근거 검사·검수 기록·원문 변경 시 승인 해제가 있으며, [jobs](src/benefitfinder/jobs/README.md#로컬-규칙-검수-실행)에서 로컬 검수 파일의 준비·승인·재검수·조회를 실행한다. 구체 모델·Agent 도구·벡터 검색은 미확정이며 실제 추출·PostgreSQL 검수 저장/운영 게시·계산 연결은 아직 없다. 데이터 처리와 모듈 경계는 [정책 데이터 분석·정제](../docs/architecture/POLICY_DATA_PIPELINE.md)를 따른다.
