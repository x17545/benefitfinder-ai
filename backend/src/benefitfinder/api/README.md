# FastAPI HTTP API

`v1/routers/health.py`·`schemas/__init__.py`와 루트 `bootstrap.py`의 공통 오류 처리는 구현했다. `/api/v1` 정책 라우터·사용 사례는 아직 구현하지 않았다. 전체 [구현 상태](../../../../docs/README.md)를 따른다.

| 경로 | 책임 |
|---|---|
| `v1/routers/` | `/api/v1`의 FastAPI 라우트와 사용 사례 호출 |
| `schemas/` | `contracts/v1/`을 따르는 Pydantic 요청·응답 모델 |
| `dependencies/` | 조립된 사용 사례를 HTTP 요청에 연결하는 의존성 함수 |

사용 사례는 application에 위임하고 정책 판정·프롬프트·수집 코드·SQL을 라우터에 넣지 않는다. 의존성 함수에서도 infrastructure를 직접 import하지 않는다. 구체 객체의 생성은 패키지 루트의 조립 지점에서 담당한다.

Pydantic 모델을 DB 모델이나 순수 domain 모델로 그대로 사용하지 않는다. 요청·응답을 변환하고 FastAPI의 기본 검증 오류도 [공통 계약](../../../../contracts/README.md)에 맞춘다. 생존 확인·공통 오류의 실제 HTTP·OpenAPI 계약은 테스트하며, 정책 API 추가 시 같은 검증을 확장한다.

정책 분석 API 구현 시 프로필을 POST 본문으로 받아 SSE `progress` 후 `result` 또는 `error` 한 번으로 종료한다. 스트림 시작 전 오류는 HTTP 상태로, 시작 후 오류는 공통 계약을 담은 `error` 이벤트로 전달한다. 요청 ID·취소·60초 타임아웃을 처리하고 프로필을 URL·로그에 남기지 않는다. 정책 상세 GET에는 공개 정보만 반환하며 개인별 진단은 현재 분석 응답을 사용한다.
