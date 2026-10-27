# FastAPI HTTP API

현재는 담당 영역을 구분하는 뼈대이며 기능은 구현하지 않았다.

| 경로 | 책임 |
|---|---|
| `v1/routers/` | `/api/v1`의 FastAPI 라우트와 사용 사례 호출 |
| `schemas/` | `contracts/v1/`을 따르는 Pydantic 요청·응답 모델 |
| `dependencies/` | 조립된 사용 사례를 HTTP 요청에 연결하는 의존성 함수 |

사용 사례는 application에 위임하고 정책 판정·프롬프트·수집 코드·SQL을 라우터에 넣지 않는다. 의존성 함수에서도 infrastructure를 직접 import하지 않는다. 구체 객체의 생성은 패키지 루트의 조립 지점에서 담당한다.

Pydantic 모델을 DB 모델이나 순수 domain 모델로 그대로 사용하지 않는다. 요청·응답을 변환하고 FastAPI의 기본 검증 오류도 [공통 계약](../../../../contracts/README.md)에 맞춘다. 실제 API 초기화 시 계약·오류 매핑을 검증한다.
