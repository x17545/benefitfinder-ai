# 공용 데이터 계약 v1 초안

프론트엔드·백엔드·AI·데이터 담당자가 공유하는 JSON 교환 형식이다. `v1/`의 JSON Schema가 필드·자료형의 기준이며, `fixtures/`는 독립 개발을 위한 가상 예제다. 아직 실행 API나 확정된 MVP 기능을 의미하지 않는다.

## 파일

| 스키마 | 예제 | 용도 |
|---|---|---|
| `benefit.schema.json` | `youth-benefit.json` | 정규화된 혜택·상세 조회 결과 |
| `profile.schema.json` | 요청 예제 안의 `profile` | 사용자 조건 |
| `recommendation-request.schema.json` | `recommendation-request.json` | 추천 요청 |
| `recommendation-response.schema.json` | `recommendation-response.json`, `empty-response.json` | 추천 결과·근거·추가 질문 |
| `error.schema.json` | `error.json` | 오류 응답 |
| `common.schema.json` | 위 예제에 포함 | 분야 식별자·출처·인용 근거 |

모든 파일은 JSON Schema Draft 2020-12를 사용한다. `$id`의 `benefitfinder.example` 주소는 로컬 참조 식별자이며 실제 서버 주소가 아니다. 검증 시 스키마를 로컬 레지스트리에 등록하므로 원격 문서를 조회하지 않는다.

## 공통 의미

- `domain`: 적용 분야. 첫 지원값은 `youth`다. 스키마는 확장을 허용하지만 서버의 지원 여부는 별도 확인한다.
- `domain_data`: 분야 고유 정보. 공통 필드나 정책 조건을 중복 저장하지 않는다. 분야별 이름·자료형은 해당 분야 문서에서 정의하고 실제 사용 전 추가 검증한다.
- `profile`: 사용자가 제공·확인한 조건. 누락 또는 `null`은 미확인을 뜻하며 `0`, 무소득, 미충족으로 바꾸지 않는다.
- `income_band`: 사용자가 선택한 소득 구간의 식별자다. 구간이 정책 기준선을 가로지르면 정확한 여유 금액이나 충족을 추정하지 않는다.
- `source`: 원문 URL, 마지막 확인 시점(`checked_at`), 문서 버전(`document_version`). 시각은 시간대가 있는 ISO 8601 형식이다.
- `evidence`: 원문의 인용문과 해당 버전의 출처. 인용문은 실제 원문에서 확인해야 하며 AI가 생성한 문장을 넣지 않는다.
- `application.status`: `open`, `closed`, `upcoming`, `unknown`. 날짜만으로 확정할 수 없는 모집 상태는 `unknown`이다.
- `condition_results[].status`: `met`(충족), `not_met`(미충족), `needs_review`(추가 확인 필요). 충족·미충족에는 최소 하나의 근거가 필요하다.
- `missing_fields`: 사용자에게 추가 확인할 필드 경로. 예: `profile.income_band`.
- `ai_generated`: 추천 설명이 AI 생성임을 표시한다. 현재 응답 계약은 `true`만 허용한다.

계약에는 최종 수급 자격 확정값을 두지 않는다. UI에서 AI 생성 표시와 최종 판단은 기관 기준이라는 안내를 제공한다. 금액 조합·현재가치·절벽 경고는 범위 확정 후 별도 계약을 추가한다.

## 아직 스키마로 검증하지 않는 의미 규칙

- 응답의 `domain`과 모든 혜택의 `domain`이 일치해야 한다.
- 요청의 `limit`을 지키고, 같은 응답에 혜택 ID를 중복해서 넣지 않는다.
- 판정 결과는 해당 혜택의 조건 ID에 연결해야 한다.
- 원문의 공신력과 인용·판정의 일치 여부를 확인해야 한다.
- 미확인 조건으로 충족을 확정하지 않는다. `missing_fields`는 사용자 조건이나 실제 분야별 필드에 연결한다.

이 규칙은 기능 구현 시 애플리케이션·분야 테스트로 검증한다. 구조 검증 통과를 자격 판정의 정확성으로 해석하지 않는다.

## 예정 HTTP 매핑

| 요청/결과 | 상태 |
|---|---|
| `POST /api/v1/recommendations` 성공 | 200, 추천 응답 |
| `GET /api/v1/benefits/{benefit_id}` 성공 | 200, 혜택 응답 |
| `invalid_request` | 422 |
| `unsupported_domain` | 400 |
| `not_found` | 404 |
| `dependency_unavailable` | 503 |
| `internal_error` | 500 |

웹 프레임워크의 기본 오류 형식도 위 공통 오류로 변환한다. 아직 이 경로와 상태 매핑은 구현하지 않았다.

## 변경과 검증

같은 PR에서 스키마·가상 예제·관련 소비자 코드를 수정하고 영향받는 담당자와 검토한다. 필수 필드 추가, 필드 제거·의미 변경, 열거형 변경은 호환성 영향을 확인한다. 공개 후 호환이 깨지는 변경은 새 버전으로 관리한다.

```bash
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

명령은 저장소 루트와 개발 가상환경에서 실행한다. 검증은 스키마 참조, 예제 형식, 미확인값·근거·확장 분야의 회귀 사례를 다룬다. 예제의 정책·기관·주소·조건·문서 버전은 모두 가상값이다.
