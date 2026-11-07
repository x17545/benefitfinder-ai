# FastAPI HTTP API

`GET /health`, 공통 오류 처리와 가상 정책을 사용하는 `POST /api/v1/recommendations`를 구현했다. 추천 응답은 3기간 × 현재가치 on/off 계산과 F13 설명을 담은 JSON이다. 실제 정책·PostgreSQL·SSE는 연결 전이다. 전체 [구현 상태](../../../../docs/README.md)를 따른다.

| 경로 | 책임 |
|---|---|
| `v1/routers/` | `/api/v1`의 FastAPI 라우트와 사용 사례 호출 |
| `schemas/` | `contracts/v1/`을 따르는 Pydantic 요청·응답 모델 |
| `dependencies/` | 조립된 사용 사례를 HTTP 요청에 연결하는 의존성 함수 |

사용 사례는 application에 위임하고 정책 판정·프롬프트·수집 코드·SQL을 라우터에 넣지 않는다. 의존성 함수에서도 infrastructure를 직접 import하지 않는다. 구체 객체의 생성은 패키지 루트의 조립 지점에서 담당한다.

Pydantic 모델을 DB 모델이나 순수 domain 모델로 그대로 사용하지 않는다. 요청·응답을 변환하고 FastAPI의 기본 검증 오류도 [공통 계약](../../../../contracts/README.md)에 맞춘다. 생존 확인·공통 오류의 실제 HTTP·OpenAPI 계약은 테스트하며, 정책 API 추가 시 같은 검증을 확장한다.

정책 분석 API 구현 시 프로필을 POST 본문으로 받아 SSE `progress` 후 `result` 또는 `error` 한 번으로 종료한다. 스트림 시작 전 오류는 HTTP 상태로, 시작 후 오류는 공통 계약을 담은 `error` 이벤트로 전달한다. 요청 ID·취소·60초 타임아웃을 처리하고 프로필을 URL·로그에 남기지 않는다. 정책 상세 GET에는 공개 정보만 반환하며 개인별 진단은 현재 분석 응답을 사용한다.

## F13 설명 실행

기본 실행은 모델 없이 `ai_generated: false`인 기본 문구를 제공한다. 로컬 Ollama에 `gemma4:e4b`가 설치돼 있으면 아래처럼 모델을 준비하고 API를 실행한다. 빈 요청으로 모델을 준비하는 방법은 [Ollama 공식 안내](https://docs.ollama.com/faq#how-can-i-preload-a-model-into-ollama-to-get-faster-response-times)를 따른다. `.env` 파일은 자동으로 읽지 않으며 환경 변수는 서버 프로세스에 전달한다.

```bash
curl --fail --silent --show-error http://127.0.0.1:11434/api/generate \
  -H 'Content-Type: application/json' \
  -d '{"model":"gemma4:e4b","keep_alive":"5m","stream":false}'
F13_OLLAMA_MODEL=gemma4:e4b uv run --locked uvicorn benefitfinder.main:app --host 127.0.0.1 --port 8000
```

다른 터미널에서 가상 계약 요청을 보낸다. 아래 경로는 Git 제외 경로이며 요청·응답 파일을 커밋하지 않는다.

```bash
mkdir -p data/local/f13-api-verification
curl --fail --silent --show-error http://127.0.0.1:8000/api/v1/recommendations \
  -H 'Content-Type: application/json' \
  --data-binary @contracts/fixtures/recommendation-request.json \
  --output data/local/f13-api-verification/response.json
```

- 각 정책의 `ai_reason`에 설명, `ai_generated`에 해당 설명의 생성 여부, `ai_sources`에 조항 원문·ID·출처·문서 버전을 담는다. 응답 최상위 `ai_generated`는 생성 설명이 하나라도 있으면 true다. 이전 응답과의 호환을 위해 정책별 새 필드는 선택 필드이며 공용 계약·웹 검증·타입을 함께 갱신했다.
- 정책당 조회·생성은 최대 5초다. 모델이 아직 준비되지 않았거나 호출·검증·검색이 실패하면 기본 문구로 돌아가고 근거 목록은 비운다. 설명 실패는 계산 성공을 실패로 바꾸지 않으며 HTTP 200과 기존 금액·판정을 유지한다. 계산 자체의 오류를 설명 실패로 처리하지 않는다.
- 같은 정책의 신청 안내는 한 요청에서만 재사용한다. 기간/PV마다 금액 문장을 새로 만들며, 사용자 프로필 전체·소득/재산·특수 조건·부모님 정보는 모델에 보내지 않는다.

실제 E4B 실행은 가상 취업·주거 정책의 생성 여부와 원문 일치를 확인하는 별도 검증이다. 오류·시간 초과·부분 성공·기간별 금액 보존은 아래 회귀 검사로 재현할 수 있다. 실제 PostgreSQL 및 정책 사실 검증을 대신하지 않는다.

```bash
uv run --locked python -m unittest backend.tests.test_recommendation_explanations -v
```
