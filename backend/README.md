# 백엔드

Python + FastAPI와 PostgreSQL을 사용하는 백엔드 구조다. 스택은 확정됐으며, 현재는 패키지·디렉터리와 안내 문서만 있다. FastAPI 실행 앱·DB 연결·런타임 의존성은 아직 초기화하지 않았다.

`src/benefitfinder/`의 모듈을 한 애플리케이션으로 통합한다. AI·데이터 수집은 담당 디렉터리를 가지며 각각 별도 서버로 배포할 필요는 없다.

기능은 [기획 명세 v2](../REQUIREMENTS_v2.md)의 F01~F11을 따른다. 선택 프로필에서 검색·자격·조합·현재가치·상황 변화·상세를 조율하고, HTTP 경계에서 SSE 진행·결과를 제공한다. 매일 갱신·검수 해제는 같은 패키지의 jobs/ingestion으로 구현한다. 실제 API·계산기·스케줄러는 아직 없다.

```text
backend/
  .env.example                  DB 연결 설정의 가상 양식
  migrations/
    versions/                   향후 PostgreSQL 스키마 변경 이력
  src/benefitfinder/
    api/
      v1/routers/               FastAPI v1 엔드포인트
      schemas/                  Pydantic 요청·응답 모델
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
  tests/                        향후 백엔드 기능 테스트
```

현재 하위 Python 패키지는 자리만 마련했으며 라우터·모델·저장소 동작은 없다. `bootstrap.py`·`main.py`·서버 설정은 실제 초기화 시 추가한다.

| 모듈 | 배치할 코드 |
|---|---|
| `api/` | FastAPI 라우트, Pydantic 요청 검증, 공통 응답·오류 변환 |
| `application/` | 사용 사례, 검색·저장·AI 등 필요한 인터페이스 |
| `domain/` | 공통 업무 모델과 순수 판정·계산 |
| `domains/youth/` | 청년정책 고유 조건과 규칙 |
| `ai/` | 공식 문서 원문 분석·추출·평가 (운영 판정 경로는 일반 코드) |
| `ingestion/` | 출처별 수집·정제·검증 |
| `infrastructure/` | PostgreSQL·검색·LLM SDK 등 구체 어댑터 |
| `jobs/` | 수집·갱신 실행과 재실행 제어 |

- 조립 코드는 기능 구현 시 `src/benefitfinder/bootstrap.py`에 둔다.
- DB 연결·트랜잭션·저장 모델·저장소 구현은 [database 안내](src/benefitfinder/infrastructure/database/README.md)를 따른다. ORM·드라이버는 아직 선택하지 않았다.
- PostgreSQL 스키마 변경은 `migrations/versions/`에 기록한다. 마이그레이션 도구는 아직 선택하지 않았으며 실제 이력·실행 설정은 없다. 원본 데이터와 API 키는 넣지 않는다.
- `.env.example`은 가상 연결 문자열 양식이다. 실제 환경 변수 로딩은 초기화 때 구현한다. 실제 `.env`·DB 비밀번호는 Git에 올리거나 프론트엔드에 전달하지 않는다.
- 기능 테스트는 `tests/`에 담당 모듈별로 추가한다. 공용 계약 검증은 저장소 루트 `tests/`에 있다.
- 각 모듈 README와 [의존성 규칙](../docs/ARCHITECTURE.md)을 따라 구현한다.
- 실행 명령과 런타임 의존성은 API 서버 초기화 후 추가하고 공통 검사·CI에 연결한다. 현재 루트 `requirements-dev.txt`는 저장소 계약 검증 전용이다.
- 가상 저장소를 이용한 단위 검증과 실제 PostgreSQL 연동 검증을 구분한다. 실제 DB 구현은 별도 테스트 DB에서 스키마 적용·읽기·쓰기를 확인한다.

2026-10-28 역할 기준: 김하정은 API·검색·판정·조합 계산·DB 연동, 양승현은 jobs 실행·스케줄·재시도·실패 알림, 윤정찬은 원천 수집·출처·버전, 탁서윤은 ingestion 분석·정제와 ai 원문 분석·규칙 후보 추출·평가를 맡는다.

제품의 핵심은 중복 수급 제한을 반영한 지원금 조합의 총액순 비교다. 데이터 Agent로 원문 규칙 후보를 추출하고 검증·사람 검수 후 게시한다. 사용자 검색·계산·템플릿 설명은 검수 데이터를 쓰는 일반 코드로 수행한다. 구체 모델·Agent 도구·벡터 검색은 미확정이며 기능은 아직 구현되지 않았다. 데이터 처리와 모듈 경계는 [정책 데이터 분석·정제](../docs/POLICY_DATA_PIPELINE.md)를 따른다.
