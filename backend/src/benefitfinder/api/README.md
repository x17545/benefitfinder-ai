# FastAPI HTTP API

`GET /health`, 공통 오류 처리와 추천·상황 변화·상세 API를 구현했다. 추천은 3기간 × 현재가치 on/off 계산과 F13 설명을 JSON 또는 POST SSE로 반환한다. `POLICY_DATABASE_URL` 설정 시 검수·게시한 PostgreSQL 정책을 계산하고, 미설정은 가상 개발 예제다. F13 조항은 `DATABASE_URL` 설정 시 PostgreSQL에서 조회한다. 실제 정책 검수·게시와 운영 연결은 후속이다. 전체 [구현 상태](../../../../docs/README.md)를 따른다.

| 경로 | 책임 |
|---|---|
| `v1/routers/` | `/api/v1`의 FastAPI 라우트와 사용 사례 호출 |
| `schemas/` | `contracts/v1/`을 따르는 Pydantic 요청·응답 모델 |
| `dependencies/` | 조립된 사용 사례를 HTTP 요청에 연결하는 의존성 함수 |

사용 사례는 application에 위임하고 정책 판정·프롬프트·수집 코드·SQL을 라우터에 넣지 않는다. 의존성 함수에서도 infrastructure를 직접 import하지 않는다. 구체 객체의 생성은 패키지 루트의 조립 지점에서 담당한다.

Pydantic 모델을 DB 모델이나 순수 domain 모델로 그대로 사용하지 않는다. 요청·응답을 변환하고 FastAPI의 기본 검증 오류도 [공통 계약](../../../../contracts/README.md)에 맞춘다. 생존 확인·공통 오류의 실제 HTTP·OpenAPI 계약은 테스트하며, 정책 API 추가 시 같은 검증을 확장한다.

## 서비스 메타데이터

`GET /api/v1/meta`는 현재 게시 자료의 가장 최근 `published_at`, 추천 계산과 같은 연 할인율·확인 상태, 오늘(KST)의 서버 전체 `daily_remaining`을 반환한다. 조회는 분석 횟수를 차감하지 않으며, 100회를 소진해도 조회할 수 있다. 변하는 잔여 횟수를 캐시하지 않도록 `Cache-Control: no-store`를 보낸다.

`POLICY_DATABASE_URL` 미설정 시에는 추천과 같은 가상 갱신 시각·가상 금리를 사용하고 `virtual_data: true`, `base_rate_status: unknown`으로 표시한다. 게시 카탈로그 모드에서는 `virtual_data: false`이며 `BASE_RATE_DATABASE_URL`의 마지막 성공한 ECOS 금리를 읽는다. 금리 미설정·빈 테이블·조회 실패 시 `base_rate: null`, `base_rate_status: unknown`이다. 게시 자료가 아직 없으면 갱신 시각을 만들지 않고 공통 503과 `Retry-After: 60`을 반환한다. 카탈로그·이용량 DB 오류도 공통 503이며 가상 자료나 메모리 횟수로 대체하지 않는다. `/health`는 이 준비 상태와 별개인 생존 확인이다. [ECOS 수동 갱신·보관](../infrastructure/database/ECOS_RATES.md)과 [공통 화면의 메타 조회](../../../../apps/web/README.md#자료-상태와-서비스-메타데이터)를 제공하며 운영 자동 갱신은 후속이다.

`POST /api/v1/recommendations`는 `Accept: text/event-stream`을 받으면 SSE로 응답하고, 생략하거나 `q=0`이면 기존 JSON을 반환한다. 프로필은 POST 본문에만 넣는다. 조건·조합 계산(`analyze_recommendations`), 서비스 조회(`search_services`), 근거 설명(`explain_with_sources`)의 실제 실행 전후에 `running`·`done`을 보낸다. 현재 계산 함수 안의 검색·판정을 별도 단계로 꾸며 보내지 않는다. 진행 이벤트는 고정 문구만 담고 프로필·금액·정책 원문을 포함하지 않는다.

검증·분야 오류는 스트림 시작 전 공통 HTTP JSON으로 반환한다. 시작 후 오류는 현재 단계의 `failed`와 공통 오류의 `error`로 끝나고, 성공은 `result` 한 번으로 끝난다. 오류 뒤 결과를 보내지 않는다. 10초 동안 새 이벤트가 없으면 주석 heartbeat를 보내며 캐시·프록시 버퍼링을 끈다. 브라우저 연결이 끊어지면 분석 코루틴을 취소한다.

추천 JSON/SSE와 상황 변화 JSON 모두 계산·서비스·설명을 합쳐 최대 60초를 적용한다. 초과 시 JSON은 HTTP 503, 스트림은 `error`의 `calculation_limit_exceeded`로 끝난다. 동기 계산은 별도 스레드에서 실행하므로 이벤트 루프를 막지 않는다. Python 스레드 자체는 강제 종료할 수 없어 이미 실행 중인 동기 DB·계산 호출은 자체 제한까지 마칠 수 있지만 늦게 도착한 결과는 전송하지 않는다. DB·정확 탐색의 자체 자원 제한은 별도로 유지해야 한다. 요청 ID·취소·시간 초과 검사는 `backend/tests/test_analysis_stream.py`에서 실제 ASGI 메시지로 검증한다.

정책 상세 GET에는 공개 정보만 반환하며 개인별 진단은 현재 분석 응답을 사용한다.

```bash
curl --no-buffer http://127.0.0.1:8000/api/v1/recommendations \
  -H 'Content-Type: application/json' -H 'Accept: text/event-stream' \
  --data-binary @contracts/fixtures/recommendation-request.json
```

## 공유 분석 이용량

추천·상황 변화는 합쳐 KST 하루 100번의 분석을 허용한다. 스키마·분야·시나리오 검증 뒤 차감하며 실패한 분석·재시도도 각각 포함한다. 한도 초과는 HTTP 429 `daily_limit_exceeded`, 이용량 DB 장애는 HTTP 503 `dependency_unavailable`이다. `ANALYSIS_USAGE_DATABASE_URL`을 설정하면 모든 서버가 PostgreSQL의 같은 날짜별 집계를 사용한다. 미설정은 재시작 시 초기화되는 단일 프로세스 개발 예제이므로 운영에서는 공유 DB 설정이 필요하다. [마이그레이션·권한·집계 범위](../infrastructure/database/ANALYSIS_USAGE.md)를 따른다. 프로필·IP·요청 ID를 저장하지 않는다.

## F13 설명 실행

PostgreSQL 게시 조항은 [명시적 마이그레이션](../../../migrations/README.md) 후 SELECT 전용 역할의 `DATABASE_URL`을 서버에 전달한다. DB를 설정한 뒤 장애·빈 조회를 가상 메모리 근거로 대체하지 않는다. [DB 저장 계약](../infrastructure/database/README.md)과 [DB→E4B→화면 통합 검증](../../../integration/README.md)을 따른다.

기본 실행은 모델 없이 `ai_generated: false`인 기본 문구를 제공한다. 개발용 모델은 [로컬 llama.cpp 설정](../../../../docs/development/LOCAL_LLM.md)에 따라 이 PC의 GPU에서 실행한다. 운영 외부 API는 같은 호출 형식에 HTTPS 주소·모델·키를 명시한다. `.env` 파일은 자동으로 읽지 않으며 환경 변수는 서버 프로세스에 전달한다.

```bash
export LLM_PROVIDER=llama_cpp
export LLM_BASE_URL=http://127.0.0.1:18080/v1
export LLM_MODEL=gemma4-e4b
export LLM_API_KEY_FILE=data/local/llama-cpp/api-key
uv run --locked uvicorn benefitfinder.main:app --host 127.0.0.1 --port 8000
```

다른 터미널에서 가상 계약 요청을 보낸다. 아래 경로는 Git 제외 경로이며 요청·응답 파일을 커밋하지 않는다.

```bash
mkdir -p data/local/f13-api-verification
curl --fail --silent --show-error http://127.0.0.1:8000/api/v1/recommendations \
  -H 'Content-Type: application/json' \
  --data-binary @contracts/fixtures/recommendation-request.json \
  --output data/local/f13-api-verification/response.json
```

- 각 정책의 `ai_reason`에 설명, `ai_generated`에 해당 설명의 생성 여부, `ai_sources`에 조항 원문·원문 위치·출처·확인일·문서 버전을 담는다. PostgreSQL 근거의 확인일은 조항이 속한 문서의 실제 DB 값을 사용한다. 응답 최상위 `ai_generated`는 생성 설명이 하나라도 있으면 true다. 이전 응답과의 호환을 위해 정책별 새 필드는 선택 필드이며 공용 계약·웹 검증·타입을 함께 갱신했다.
- [결과 화면](../../../../apps/web/README.md#f13-결과-설명과-원문-근거)은 정책별 설명과 AI/기본 안내를 표시하고, 원문 근거를 펼쳐 인용·버전·확인일·링크를 보여준다. 실패·시간 초과의 빈 `ai_sources`에는 근거 펼침을 만들지 않는다.
- 정책당 조회·생성은 최대 5초다. 모델이 아직 준비되지 않았거나 호출·검증·검색이 실패하면 기본 문구로 돌아가고 근거 목록은 비운다. 설명 실패는 계산 성공을 실패로 바꾸지 않으며 HTTP 200과 기존 금액·판정을 유지한다. 계산 자체의 오류를 설명 실패로 처리하지 않는다.
- 같은 정책의 신청 안내는 한 요청에서만 재사용한다. 기간/PV마다 금액 문장을 새로 만들며, 사용자 프로필 전체·소득/재산·특수 조건·부모님 정보는 모델에 보내지 않는다.

실제 E4B 실행은 가상 취업·주거 정책의 생성 여부와 원문 일치를 확인하는 별도 검증이다. 오류·시간 초과·부분 성공·기간별 금액 보존은 아래 회귀 검사로 재현할 수 있다. 실제 PostgreSQL 및 정책 사실 검증을 대신하지 않는다.

```bash
uv run --locked python -m unittest backend.tests.test_recommendation_explanations -v
```

## S4 상황 변화와 S5 정책 상세

`POST /api/v1/simulations`는 취업·연 소득 증가·독립·나이 +1에 대해 추천과 같은 가상 정책·조합 계산을 사용한다. 전후 카드 총액은 선택 기간·현재가치 기준이고, 실효한계세율은 가정한 연 소득과 할인하지 않은 1년 지원금으로 계산한다. 소득을 모르는 경우 임의로 정하지 않는다. 이미 혼자라면 독립 시나리오에서 조건을 바꾸지 않는다. 조합 교체와 자격 기준 이탈을 구분하고, 실제 공고 근거가 없으므로 `todo`는 비운다.

`GET /api/v1/benefits/{benefit_id}`는 같은 정책 ID·출처·버전의 공개 상세를 반환한다. 화면은 현재 분석과 일치할 때만 개인별 진단을 연결한다. 신청 주소나 날짜를 만들어 넣지 않는다. 두 응답의 `virtual_data: true`는 백엔드가 가상 정책을 사용했다는 뜻이다. 실정책 DB 연결, 실제 모집·신청 안내와 공유 하루 상한은 후속이다.

백엔드 실행 후 아래 명령으로 가상 주거 시나리오와 가상 취업 정책을 재현할 수 있다. 결과 파일은 Git 제외 경로에 둔다.

```bash
mkdir -p data/local/s4-s5-api
curl --fail --silent --show-error http://127.0.0.1:8000/api/v1/simulations \
  -H 'Content-Type: application/json' \
  --data-binary @contracts/fixtures/simulation-request.json \
  --output data/local/s4-s5-api/simulation.json
curl --fail --silent --show-error http://127.0.0.1:8000/api/v1/benefits/youth%3Aexample%3AP3 \
  --output data/local/s4-s5-api/employment-detail.json
```

## F14 서비스 추천과 공개 상세

`POST /api/v1/recommendations`는 기존 6개 현금 결과와 별도로 `services`·`services_total_won`을 반환한다. 현재는 가상 취업·주거 카탈로그에서 선택한 관심 분야와 입력 조건을 적용한다. 미충족·모집 종료·예정은 제외하고 확인 필요는 진단과 함께 남긴다. 확인된 단가·근거 설명·출처 URL·확인일이 모두 있을 때만 단가 × 횟수를 계산한다. 유효한 환산이 없으면 합계는 `null`이다.

`GET /api/v1/benefits/{benefit_id}`의 서비스형 응답에는 같은 정책 ID·문서 버전의 공개 `service`가 있다. 입력에 따른 진단은 추천 응답에만 담는다. 모집 일정은 한국 날짜 기준이며 마감 당일을 포함한다. 모집 전·마감 후에는 목록과 합계에서 빼고 상세에는 상태를 표시한다. 아직 도래하지 않은 확인일의 단가는 환산하지 않는다. `POLICY_DATABASE_URL` 설정 시 [게시 서비스 카탈로그](../infrastructure/database/SERVICE_CATALOG.md)를 같은 DB에서 읽는다. 승인 해시·원천 변경 무효화·단가 근거를 검사하며 상세와 메타에 연결한다. 실제 정책·단가 자료 검수/게시와 F12 후보의 검수 인계는 후속이다.

로컬 FastAPI와 웹을 실행하고 27세·경기도 수원시·무주택·월세로 13단계를 입력한다. 구직 중/무소득/취업 관심은 S3 현금 540만 원, S6 정장 대여 12만 원과 단가 미상 상담이 나온다. 재직/연 소득 1,500만~3,000만/주거 관심은 S3 현금 240만 원, S6 이사 차량 25만 원과 단가 미상 상담이 나온다(현금은 1년 명목 기준). 서비스 행을 눌러 내용·근거·진단을 확인하고, 직접 주소 접근에서는 개인 진단이 없음을 확인한다. 모두 가상 정책이다.

API만 재현하려면 공용 가상 요청을 사용한다. 응답은 Git 제외 경로에 저장한다.

```bash
mkdir -p data/local/f14-service-api
curl --fail --silent --show-error http://127.0.0.1:8000/api/v1/recommendations \
  -H 'Content-Type: application/json' \
  --data-binary @contracts/fixtures/recommendation-request.json \
  --output data/local/f14-service-api/recommendation.json
curl --fail --silent --show-error http://127.0.0.1:8000/api/v1/benefits/youth%3Aexample%3AS2 \
  --output data/local/f14-service-api/suit-detail.json
```
