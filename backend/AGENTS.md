# 백엔드 추가 지침

- 루트 `AGENTS.md`, `backend/README.md`, `docs/ARCHITECTURE.md`를 따른다. Python 3.12를 사용한다. 루트 uv 프로젝트에 속하므로 `backend/`에 별도 가상환경·requirements 파일을 만들지 않는다. `uv sync --locked`, `uv add`/`uv add --dev`, `uv run --locked`를 사용하며 pip로 직접 설치하지 않는다.
- 백엔드는 Python + FastAPI, DB는 PostgreSQL로 확정했다. 다른 프레임워크·DB로 임의 교체하지 않는다. ORM·드라이버·마이그레이션 도구·LLM 공급자는 아직 선택하지 않았다.
- `api`는 요청·응답, `application`은 사용 사례·인터페이스, `domain`은 공통 모델·순수 판정, `domains/<분야>`는 고유 규칙을 담당한다.
- `domain`은 표준 라이브러리와 자기 모듈에만 의존한다. `application`은 `domain`과 자기 모듈을 사용하며 구체 어댑터를 import하지 않는다.
- `api`는 `application`·`domain`을 사용한다. `ai`·`ingestion`·`infrastructure`는 서로를 직접 import하지 않고 인터페이스로 연결한다.
- FastAPI 라우터는 `api/v1/routers/`, Pydantic 요청·응답 모델은 `api/schemas/`, HTTP 의존성은 `api/dependencies/`에 둔다. HTTP 의존성 함수에서 구체 어댑터를 만들지 말고 조립된 사용 사례를 연결한다.
- 구체 구현 조립은 패키지 루트의 `bootstrap.py`, `main.py`, `__main__.py` 또는 `jobs/__main__.py`에 제한한다. 일반 모듈을 조립 파일 이름으로 바꿔 검사를 우회하지 않는다.
- DB·LLM·검색·원천 HTTP 클라이언트의 구현은 `infrastructure`에 둔다. 도메인 코드에 SDK나 DB 테이블을 섞지 않는다.
- PostgreSQL 연결·트랜잭션과 저장 모델·저장소는 `infrastructure/database/`에 둔다. API 스키마·순수 domain 모델과 DB 모델을 구분한다. AI·수집 모듈에는 저장소 인터페이스를 주입한다.
- DB 변경은 `migrations/versions/`의 이력과 데이터 영향을 함께 검토한다. 공유 환경에 적용한 이력은 새 마이그레이션으로 고치고, import·서버 시작 시 자동 DDL로 대신하지 않는다. 운영 DB에 임의 적용하지 않는다.
- DB 연결 문자열과 비밀값은 백엔드 환경 변수로 전달한다. `.env.example`은 가상 양식이며 실제 `.env`와 자격 증명은 Git·로그·프론트엔드에 넣지 않는다.
- 기능 테스트는 `backend/tests/`에 추가하며 하위 테스트 패키지는 테스트 러너에서 실제 발견되게 구성한다. 도구를 바꾸면 공통 검사도 함께 갱신한다.
- FastAPI 초기화 시 실제 HTTP 요청·응답·오류 계약을 검사한다. PostgreSQL 연동과 마이그레이션은 별도 테스트 DB에서 확인한다. 가상 저장소·SQLite로만 검사하고 PostgreSQL 검증 완료라고 보고하지 않는다.
- 예외를 성공 응답으로 숨기지 않는다. 사용자 원문·소득·프롬프트·키 전체를 로그에 남기지 않는다.
