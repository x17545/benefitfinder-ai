# 공용 데이터 계약 v1 초안

프론트엔드·백엔드·AI·데이터 담당자가 공유하는 JSON 교환 형식이다. `v1/`의 JSON Schema가 필드·자료형의 기준이며, `fixtures/`는 독립 개발을 위한 가상 예제다. 2026-10-27 추가 회의에 따라 초기 MVP의 수집·검색·진단·조합·상황 변화·상세는 청년 취업·주거 혜택만 다룬다. 생활·자산형성·교육·장학 등은 확장 단계다. F01~F11과 13단계 입력은 두 분야에 유지하지만, 정책 API는 아직 없고 6장 필드명/형식과 11.2 기본값(동점 순서, 취업 가정 연 소득 2,400만, 기준선 ±100만 등)은 회의 논의 초안이다. 원초안의 상위 20개 절단은 최적값을 누락할 수 있어 이번 계산 규칙 정리에서 제거했다.

## 파일

| 스키마 | 예제 | 용도 |
|---|---|---|
| `benefit.schema.json` | `youth-benefit.json` | 정규화된 혜택·상세 조회 결과 |
| `profile.schema.json` | 요청 예제 안의 `profile` | 사용자 조건(공통) |
| `youth-profile.schema.json` | `recommendation-request.json`의 `profile` | 청년 13단계 확장. `domain==youth`일 때만 적용 |
| `recommendation-request.schema.json` | `recommendation-request.json` | 추천 요청 |
| `recommendation-response.schema.json` | `recommendation-response.json`, `fallback-response.json`, `empty-response.json`, `pv-unknown-response.json` | 조합 결과·근거(3기간×PV on/off, view·탭별 계산 가능 구분) |
| `simulation-request.schema.json` | `simulation-request.json` | 상황 변화 요청 |
| `simulation-response.schema.json` | `simulation-response.json` | 상황 변화 결과·실효비율 |
| `meta.schema.json` | `meta.json` | 데이터 갱신·기준금리·남은 횟수 |
| `options.schema.json` | `options.json` | 지역·구간 선택지 |
| `sse-progress.schema.json` | `sse-progress.json` | SSE 진행 이벤트 data |
| `error.schema.json` | `error.json` | 오류 응답 |
| `health.schema.json` | - | GET /health 생존 확인 응답 |
| `common.schema.json` | 위 예제에 포함 | 분야 식별자·출처·인용 근거 |

모든 파일은 JSON Schema Draft 2020-12를 사용한다. `$id`의 `benefitfinder.example` 주소는 로컬 참조 식별자이며 실제 서버 주소가 아니다. 검증 시 스키마를 로컬 레지스트리에 등록하므로 원격 문서를 조회하지 않는다.

## 공통 의미

- `domain`: 적용 분야. 첫 지원값은 `youth`다. 스키마는 확장을 허용하지만 서버의 지원 여부는 별도 확인한다.
- 초기 정책 분류: `domain: youth` 안에서 `categories`의 `employment`(취업)·`housing`(주거)만 활성화한다. 관심 선택 `interests`도 이 두 값만 받는다. 수집 출처의 분류를 검수하여 대응시키고 혼합·불명확한 분류는 확인 전 노출하지 않는다. 공통 혜택의 `categories`와 분야 식별자는 확장 가능하게 유지하며, 확장 시 청년 관심 enum·선택지·후보 필터·예제·검증을 함께 갱신한다. 현재 실행 API나 배포된 소비자는 없으며, 이번 관심 enum 축소는 MVP 회의 결정에 맞춘 초안 계약 변경이다.
- `domain_data`: 분야 고유 정보. 공통 필드나 정책 조건을 중복 저장하지 않는다. 분야별 이름·자료형은 해당 분야 문서에서 정의하고 실제 사용 전 추가 검증한다.
- `profile`: 사용자가 제공·확인한 조건. 공통 `age`/`region_code`/`employment_status`/`income_band`/`household_type`은 유지하고, 혼인·학력·주거·계약/보증금/월세·재산·특수조건·관심·이미수혜·가구/부모정보는 `domain_data`에 둔다. 지역은 공통 `region_code`(시군구 전체 코드)가 SSOT이며, `domain_data.region`에는 피커 상위 시도(`sido_code`)만 둔다. 기간+현재가치 옵션은 `request.options` 한 곳에만 둔다. 누락 또는 `null`은 미확인을 뜻하며 `0`, 무소득, 미충족으로 바꾸지 않는다.
- `military_months_range`: `{min, max}`. 정확한 개월은 동일값, 미상은 전체 `null`, 열린 상한은 `max: null`. UI 매핑은 해당없음 `0..0`, 1년6개월이하 `0..18`, 1년6개월초과~2년 `19..24`, 2년초과 `25..null`이다. `min<=max`는 스키마가 강제하지 않고 애플리케이션 검증과 테스트로 확인한다. 정책별 최대 6년 연장은 해당 공고 근거가 있을 때만 적용하고 근거 없으면 사용하지 않는다.
- `income_band`: 사용자가 선택한 소득 구간의 식별자다. 구간이 정책 기준선을 가로지르면 정확한 여유 금액이나 충족을 추정하지 않는다.
- `source`: 원문 URL, 마지막 확인 시점(`checked_at`), 문서 버전(`document_version`). 시각은 시간대가 있는 ISO 8601 형식이다.
- `evidence`: 원문의 인용문과 해당 버전의 출처. 인용문은 실제 원문에서 확인해야 하며 AI가 생성한 문장을 넣지 않는다.
- `application.status`: `open`, `closed`, `upcoming`, `unknown`. 상시 모집은 별도 `always` 값을 두지 않고 `open`+공식 상시 근거로 표현한다. 날짜만으로 확정할 수 없는 모집 상태는 `unknown`이며, 날짜 `null`을 `open`으로 추정하지 않는다.
- `condition_results`/`eligibility[].status`: `met`(충족), `not_met`(미충족), `needs_review`(추가 확인 필요). `need_check` 표기는 거부된다. 충족·미충족에는 최소 하나의 근거가 필요하다.
- `ai_generated`: 결과 설명을 생성형 AI가 작성했으면 `true`, 기본 근거 템플릿이면 `false`다. 데이터 준비에서 Agent를 썼는지를 나타내는 플래그가 아니다. `ai_reason`(6장 초안 이름 유지)은 템플릿을 포함한 추천 이유 텍스트이며 이 플래그와 함께 해석한다. 최종 필드명은 회의에서 정리한다. 데이터 Agent의 활용은 이용 안내에 별도로 설명하며 사용자 검색·코드 판정/계산·템플릿 응답은 LLM 없이 동작한다.
- `conflicts`: 현재는 근거를 가진 단순 금지 쌍만 표현한다. 조건부·기간별·수급 이력·금액 차감 규칙을 여기에 손실 변환하지 않는다. Agent 추출 후보는 적용 정책·조건·예외·시점·원문 버전과 함께 검수하며, 복잡한 규칙의 내부 모델·코드 지원 범위·공유 계약은 다음 회의에서 검토한다. API 필드 누락은 동시 수급 허용의 근거가 아니다. 상세는 [정책 데이터 분석·정제 설계](../docs/POLICY_DATA_PIPELINE.md)를 따른다.
- 금액은 모두 원 단위 정수이며, 조합에는 검수 확정 금액만 포함한다. 만기·현재가치 지급 시점이 불명확하면 `needs_review`로 남긴다. 기준금리가 미확인(`discount_rate: null`, `discount_rate_status: unknown`)일 때 `0` 할인율로 추정하지 않고, 해당 탭의 `result_view`를 `calculation_status: unavailable`+`unavailable_reason: missing_discount_rate`(지급 시점 불명확이면 `unknown_payment_timing`)로 둔다. `unavailable` 분기는 `portfolios` 빈 배열·합계 추정 안 함이 강제되며 정상 원금액(raw) 분기만 활성이다.
- 실효한계세율=(1년 지원금 감소분/1년 소득 증가분)×100이며 세금·4대보험을 제외한 지원금 변화만 본다.

가상 응답은 현행 기획 8.3의 취업·주거 정책 P1·P3·P6로 6개 탭의 조합·합계를 채운다. 1년 최상위는 540만 원, 2년·전체는 600만 원이며, 금리 미확인 예제도 원금액 결과는 같다. 미확인 중복 쌍 경고는 별도 화면 상태 예시다. 현행 정답은 `tests/fixtures/mvp_planning_rules.json`, 이전 5개 정책의 `planning_rules.json`은 과거·확장 회귀 자료다. 현재가치는 정책별 현금흐름을 할인한 뒤 원 단위로 반올림하고, 조합 합계는 표시하는 정책 금액의 합으로 맞춘다. 만기형의 기간 누적 기대액은 그 기간의 현금 수령액과 구분하며 실제 만기 시점으로 할인한다.

계약에는 최종 수급 자격 확정값을 두지 않는다. 결과 문장의 AI 생성 표시는 실제 설명 생성 여부에 맞추고, 데이터 Agent 사용 안내와 구분한다. 최종 판단은 기관 기준이라는 안내를 제공한다.

## 민감 정보 취급

다음 계약 필드는 LLM·임베딩·GA·서버 로그에 보내지 않으며, 추천 이유 문장에도 쓰지 않는다.

- `profile.domain_data.special` (9단계 특수 조건)
- `profile.domain_data.household.parent_income_band` (부모님 소득)
- `profile.domain_data.household.parent_asset_band` (부모님 재산)

서버는 판정 코드로만 사용하고 저장하지 않는다. GA에는 조건 값을 보내지 않고 단계·소요 시간·오류 코드 등만 보낸다.

## 아직 스키마로 검증하지 않는 의미 규칙

- 응답의 `domain`과 모든 조합 정책의 `domain` 성격이 일치해야 한다.
- 같은 결과에 정책 ID를 중복해서 넣지 않고, 상위 조합은 최대 3개다.
- 판정 결과는 해당 혜택의 조건 ID에 연결해야 한다.
- 원문의 공신력과 인용·판정의 일치 여부를 확인해야 한다.
- 값이 완전히 비어 있을 때 충족으로 단정하지 않는다. 구간 값이 조건을 완전히 충족하거나 완전히 실패하면 근거와 함께 확정 판정(`met`/`not_met`)하고, 범위가 기준선에 걸칠 때만 `needs_review`로 표시한다.
- `special`의 `none`은 단독으로만 사용하고, 기혼이면 부모 정보는 `null`로 둔다.
- 전역 `view`는 초기 기준(기본 선택 1년+현재가치 끔, 즉 `results[1y][pv_off]`의 결과와 일치)이다. 기간/PV 탭 전환 시 화면은 다시 묻지 않고 선택 탭의 `result_view`를 따라 표시 섹션을 바꾼다(조합이 있으면 카드, 없으면 그 탭의 `fallback_list` 최대 5개, 둘 다 없으면 비어 있음). 두 정책 이상을 함께 받을 조합이 없고 단일 정책만 가능하면 목록으로 표시한다. 두 정책 이상의 유효한 조합 하나는 조합 카드다. 목록/비어 있음은 통상 6가지 탭에서 동일하다.
- `todo`는 공고 원문 근거(`evidence` 1개 이상 필수)가 있을 때만 제공하고, 없으면 `todo` 전체를 `null`로 둔다. 근거 없는 `todo`는 계약 위반이다.
- 시뮬레이션의 `before/after_total_won`은 선택 기간 카드합, `annual`은 연간 환산 지원금이다. `income_change`의 `from/to`는 연간(annual) 대표값이자 가상 시나리오 가정이며, `assumption_note`로 그 뜻을 함께 표시하고 사용자 확정 소득으로 표시·저장하지 않는다. 변동 카드의 `policy_id`로 추천 결과의 조합 정책 및 `GET /api/v1/benefits/{benefit_id}` 상세의 `source`·`evidence`에 연결하고, 카드 자체에 근거를 중복하지 않는다.

이 규칙은 기능 구현 시 애플리케이션·분야 테스트로 검증한다. 구조 검증 통과를 자격 판정의 정확성으로 해석하지 않는다.

## HTTP 매핑 (현재 구현: GET /health, 나머지는 예정)

| 방식 | 주소 | 하는 일 | 응답 |
|---|---|---|---|
| GET | `/api/v1/meta` | 갱신 시각·기준금리·남은 횟수 | `meta` |
| GET | `/api/v1/options` | 지역·구간 선택지 | `options` |
| POST | `/api/v1/recommendations` | 분석 시작. SSE `progress` 여러 번 후 최종 `result` 한 번(또는 `error`) | `recommendation-response` |
| POST | `/api/v1/simulations` | 상황 변화 계산 | `simulation-response` |
| GET | `/api/v1/benefits/{benefit_id}` | 정책 상세 | `benefit` |
| GET | `/health` | 생존 확인(liveness 전용, DB-ready 보장 안 함) | `health` |

SSE 오류 규칙: HTTP 헤더를 보내기 전 오류는 해당 상태코드+`error` 스키마로 응답하고 스트림을 열지 않는다. 스트림 시작 후 오류는 SSE `error` 이벤트 1회로 알리고 스트림을 종료하며, 그 뒤에 `result`를 보내지 않는다.

| `code` | 상태 | 언제 |
|---|---|---|
| `http_error` | 원래 HTTP 상태 | 아래 기본 매핑에 없는 명시적 HTTP 오류(400·401·403·418·429·502 등) |
| `invalid_request` | 422 | 필수 값 없음, 범위 밖 값 |
| `unsupported_domain` | 400 | 미지원 분야 |
| `not_found` | 404 | 없는 경로·정책 ID |
| `method_not_allowed` | 405 | 지원하지 않는 HTTP 메서드 |
| `daily_limit_exceeded` | 429 | 하루 100회 초과 |
| `dependency_unavailable` | 503 | LLM·임베딩·외부 API 실패 |
| `calculation_limit_exceeded` | 503 | 계산 상한 초과 |
| `internal_error` | 500 | 계산·DB 등 내부 오류 |

웹 프레임워크의 기본 오류 형식도 위 공통 오류로 변환한다. FastAPI 부트스트랩은 명시적인 `HTTPException`의 상태코드와 `Retry-After`·`WWW-Authenticate`·`Allow` 등 응답 헤더를 보존한다. 본문을 JSON으로 교체하므로 Content-Type·Content-Length는 재생성하고 기존 Content-Encoding·Transfer-Encoding은 전달하지 않는다. 기본 코드는 404→`not_found`, 405→`method_not_allowed`, 422→`invalid_request`, 500→`internal_error`, 503→`dependency_unavailable`, 나머지는 `http_error`다. HTTP 상태만으로 미지원 분야·하루 사용량 초과·계산 상한 초과를 추정하지 않으며, 이 업무 오류는 기능 구현 시 명시적으로 분류한다. 요청 검증 오류는 422, 예기치 않은 일반 예외는 500으로 반환한다. 오류마다 UUID `request_id`를 부여하고 입력값·예외 원문을 포함하지 않는다.

## 변경과 검증

같은 PR에서 스키마·가상 예제·관련 소비자 코드를 수정하고 영향받는 담당자와 검토한다. 필수 필드 추가, 필드 제거·의미 변경, 열거형 변경은 호환성 영향을 확인한다. 공개 후 호환이 깨지는 변경은 새 버전으로 관리한다.

이번 정리(v1 초안 내, 배포된 소비자·정책 API 없음 — 미배포 형식 변경): 미배포 `request.limit` 호환 필드 삭제, 지역 중복 제거(`region_code` SSOT, `domain_data.region`은 `sido_code`만), 요청 `message` 필드 삭제(문장 입력 없음, 13단계 단일 계약), 응답 `follow_up_questions` 필드 삭제(결과 추가 질문 없음, 빈 배열 포함 전부 거부), 기간+현재가치 옵션은 `request.options` 단일 위치로 정리(`domain_data.options` 삭제, 시뮬레이션 요청에도 동일 `options`), 탭별 계산 가능 표시(`calculation_status`+`unavailable_reason`, 스키마 강제; `portfolio.total_status` 삭제, 합계 미상은 `unavailable` 분기로), 시뮬레이션 응답은 선택 기간 카드합(`before/after_total_won`)+연간 환산(`annual`) 분리 및 `income_change.assumption_note` 필수, 시뮬레이션 `todo`는 `evidence` 1개 이상 필수, 조합 목록(`fallback_list`)은 6.3 예시처럼 선택 탭별 `result_view` 안으로 이동(전역 `view`는 초기 기준), 오류 코드 2종(`daily_limit_exceeded`, `calculation_limit_exceeded`) 추가. 기존 `profile` 공통 필드, `source`/`evidence`, `met`/`not_met` 근거 필수, `needs_review`, 오프라인 `$ref` 검증은 유지된다. `ai_generated` boolean(템플릿 false 기본)+`ai_reason` 설명 일반화, SSE `step` 일반 처리 단계 예시.

```bash
uv sync --locked
uv run --locked python scripts/check.py
pnpm types:generate  # 스키마 변경 후 생성 타입도 커밋
```

명령은 저장소 루트와 개발 가상환경에서 실행한다. 검증은 스키마 참조, 예제 형식, 미확인값·근거·확장 분야의 회귀 사례를 다룬다. 예제의 정책·기관·주소·조건·문서 버전은 모두 가상값이다.
