# 게시 정책 카탈로그

`POLICY_DATABASE_URL`을 설정하면 추천·상세·상황 변화 API가 `policy_catalog_releases`의 현재 게시 버전을 사용한다. 미설정 실행은 기존 가상 예제다. 설정 후 조회 실패는 `503 dependency_unavailable`이며 가상 정책이나 빈 성공 결과로 대체하지 않는다. DB가 비어 있으면 정상 빈 결과다. import·서버 시작 때 접속·DDL·초기 데이터·게시를 실행하지 않는다.

관리 역할로 [마이그레이션](../../../../migrations/README.md)의 0001·0002를 적용한 뒤 런타임에는 별도 읽기 역할을 사용한다. 실제 정책 게시에는 수집 영역 0005와 [원천 변경·재검수 0006](POLICY_SOURCE_RECHECKS.md)이 필요하며 기존 실제 게시 자료를 검수 대기로 내리는 데이터 영향을 먼저 확인한다.

```sql
GRANT USAGE ON SCHEMA public TO benefitfinder_reader;
GRANT SELECT ON public.policy_catalog_releases TO benefitfinder_reader;
```

## 입력과 검수

내부 적재 형식은 `infrastructure/policy_catalog_records.py`의 `CatalogRecord`다. 외부 응답은 `contracts/v1`이며 신청 안내는 선택 필드로 확장했다. 실제 입력·원문·승인 파일은 Git 제외 `data/local/catalog/` 등에 보관한다.

| 필드 | 내용 |
|---|---|
| `benefit` | 공용 benefit 형식의 청년 취업·주거 현금 정책. 출처·신청 상태·조건별 인용과 `reviewed`, `virtual_data` 보존 |
| `conditions` | 모든 자격 조건 ID에 대응하는 실행 규칙. 문장만 있는 조건은 `manual` |
| `payments` | 검수한 실제 지급 일정 `[{"month": 1, "amount_won": 1000000}]`. 미확인은 `null` |
| `payment_evidence` | 금액·시점의 인용·위치·출처. benefit과 같은 버전·확인일 |
| `maturity_accruals` | 만기형의 별도 검수된 지원금 적립 증가분 목록. `month`·`amount_won`은 증가분이며 누적 잔액이 아니다. 본인 납입금 제외. 미확인은 생략 |
| `maturity_accrual_evidence` | 적립 일정의 인용·위치·같은 원문 버전. 적립 목록과 함께 제공하고 전체 승인 해시에 포함 |
| `unresolved_combination_rules` | 미해결 중복·차감·예외 규칙이 있으면 `true`. 이때 `benefit.reviewed: true`는 거부 |
| `source_sha256` | 별도 보관한 UTF-8 원문 텍스트의 정확한 SHA-256 |
| `explanation_clauses` | 선택한 F13 조항의 ID·제목·원문 인용·토픽. 전체 승인 해시에 포함하고 0010에서 같은 버전으로 원자적 게시 |
| `f12_review` | 선택적 원본 쌍·판단·내보내기·대응표 해시와 마지막 검수 시각. [F12 입력 연결](F12_CATALOG.md)에서 생성하며 별도 전체 승인이 필요 |
| `collection_sources` | 검수한 수집 원천의 `source`, `external_id`, `material_sha256` 목록. 실제 정책은 필수, 최대 20개이며 전체가 승인 해시에 포함됨. 원문 텍스트 해시와 구분 |

실행 규칙은 다음 범위만 지원한다. 다른 연산자·필드·불완전한 매개변수는 거부한다.

- `manual`: `condition_id`, `kind`만 기록하며 항상 확인 필요다.
- `age`: `minimum`, `maximum`, `military_extension_years`(0~3). 완전한 복무 연수를 상한까지만 인정하는 공고에만 사용한다. 다른 연장 방식은 `manual`이다. 복무 구간 전체를 비교하며 하나의 확정값으로 바꾸지 않는다.
- `income_max`: 연 소득 원 단위 `maximum`, 경계 포함 `inclusive`. 구간 전체를 비교한다. 중위소득 비율·가구별 환산표는 추정하지 않고 `manual`이다.
- `selection` / `none_of`: 명시적 `field`, `values` 중 하나 이상 선택 / 전부 미선택을 판정한다. 허용 필드는 코드의 `SELECTION_FIELDS`이며 고용·지역·학력·혼인·주거·자가·계약·자산 선택·특수 조건·기수혜 선택을 지원한다. 미입력·모름은 확인 필요다. 생애 1회 정책은 기수혜 `none_of` 규칙을 함께 기록해야 검수 완료로 게시할 수 있다.

모든 인용은 지정 원문에 실제로 포함되어야 한다. 검수 완료 정책은 확정 금액·지급 시점·일정 합계·미해결 중복 규칙 부재를 요구한다. 공식 출처와 공고 의미·코드 표현의 일치는 사람이 검수한다. 서명 없는 승인 파일은 검수자 인증을 대신하지 않는다. [F12 입력 연결](F12_CATALOG.md)은 사람이 작성한 실행 규칙·대응표를 요구하며 별도 전체 승인 없이 게시하지 않는다.

### 신청 안내 (F07)

`benefit.application_guide`는 현금·서비스 공통의 선택 필드다. `method`, `office`는 각각 `{quote, location, source}` 또는 `null`이며, `documents`는 같은 형태의 인용 목록(최대 100개) 또는 `null`이다. 인용마다 같은 정책 출처·버전·확인일과 실제 원문의 문장이 필요하다. 한 인용의 최대 길이는 2,500자, 위치는 500자이며 원문을 임의로 잘라 맞추지 않는다. 긴 목록은 의미를 보존한 원문 구획별로 나누고 사람이 확인한다.

안내 객체 생략/`null`과 `documents: null`은 아직 확인하지 못한 상태다. `documents: []`는 검수자가 해당 원문에 서류 안내가 없음을 확인한 경우에만 쓴다. `method`/`office`가 없으면 공식 페이지 확인을 안내하고 운영 기관으로 접수 기관을 채우지 않는다. 준비 서류가 있을 때는 원문 그대로 표시한다. 확인하지 못한 자료를 '공식 자료에 없음'으로 표시하지 않는다.

안내는 전체 승인 해시에 포함되므로 내용·누락 상태가 달라지면 새 문서 버전과 새 검수가 필요하다. 안내가 없는 기존 입력의 정규화 형태·승인 해시는 그대로 유지한다. JSONB에 함께 저장하므로 새 SQL 마이그레이션은 없다. F12 추출·수집 원천이 안내를 포함해도 게시 입력이나 사람 승인을 자동으로 만들지 않는다.

다음 검증은 DB를 수정하지 않으며 정규화한 입력 전체의 해시를 출력한다.

```sh
uv run --locked python -m benefitfinder.jobs policy-catalog validate \
  --input data/local/catalog/policy.json --source data/local/catalog/source.txt
```

사람이 원문·금액·지급 시점·중복·실행 규칙을 검수한 뒤 별도 승인 JSON을 작성한다. 필드는 `record_sha256`(위 검증값), `reviewed_by`(실제 검수자), `reviewed_at`(시간대 포함 시각) 세 개다. 데이터가 바뀌면 새 검수가 필요하다. 검증 명령은 승인을 생성하지 않는다.

게시 관리 역할을 `POLICY_PUBLISH_DATABASE_URL`로 전달하고 명시적으로 실행한다. 환경 변수 파일을 자동으로 읽지 않는다.

```sh
uv run --locked python -m benefitfinder.jobs policy-catalog publish \
  --input data/local/catalog/policy.json --source data/local/catalog/source.txt \
  --approval data/local/catalog/approval.json
```

승인 해시·원문 해시·인용·검수 시각을 확인한 뒤 한 트랜잭션에서 이전 버전을 은퇴시키고 새 버전을 게시한다. 같은 정책/버전을 덮어쓰지 않는다. 실패 시 이전 게시 상태까지 롤백한다. 저장 데이터가 승인 해시와 달라지면 조회도 실패한다. 공유·운영 DB 적용은 별도 승인 범위를 따른다.

## AI 독립 검수 배치 (0013)

사람 승인 명령은 그대로 유지한다. 별도의 `ai-catalog` 경로는 작성자와 다른 검수자의 ID, 검수 모델, 판정·시각, 문서 버전·원문 SHA-256과 전체 카탈로그 레코드 SHA-256을 기록한다. 이 파일은 서명이 아니며 에이전트 신원 인증 자체를 제공하지 않는다. 실제 작업에서 배치 산출물과 검수 기록의 출처를 확인해야 한다. `pass` 이외, 작성자와 검수자가 같거나 시각·해시가 맞지 않는 경우를 거부한다. 원문·인용·수집 원천 해시와 현재 DB 상태는 기존 검사와 트리거를 그대로 적용한다. 실제 AI 배치는 현재 온통청년 원천 하나에만 연결하고, 저장 필드에서 정해진 순서로 만든 본문·정책 ID·공식 상세 URL·내용 해시 버전을 DB에서 대조한다. 별도 단가 원문이 필요한 실제 서비스와 복수 원천 정책은 이 경로에서 거부한다. 게시 역할에는 수집 원천의 `source`, `external_id`, `fields`, `material_sha256`, `missing_detected_at` 열 SELECT가 추가로 필요하다. 조건·금액·시점·중복이 확인되지 않은 현금 정책을 검수 완료 계산 대상으로 만들지 않는다.

0012까지 적용한 뒤 `0013_ai_catalog_review.sql`을 별도 트랜잭션으로 명시적으로 적용한다. 기존 행의 검수 유형은 `human`으로 유지되며, AI 행은 `ai`와 AI 검수 자료가 불변으로 기록된다. 애플리케이션 시작 시 DDL·게시를 실행하지 않는다. 운영 적용·코드 배포는 별도 승인 및 DB 담당자 검토 후 실행한다.

manifest는 최대 50개 항목의 JSON 배열이며 각 항목은 `input`, `review`, `source` 파일 경로와 필요한 경우 `price_source` 경로를 가진다. 경로는 manifest 위치 기준이다. `input`은 기존 현금 또는 서비스 카탈로그 형식이며 `review`의 필드는 `writer_agent`, `reviewer_agent`, `reviewer_model`, `verdict`, `reviewed_at`(시간대 포함), `document_version`, `source_sha256`, `record_sha256`이다. 내용과 근거를 검수한 뒤 생성한 파일을 사용한다. 원천·검수·manifest는 Git 제외 경로에 보관한다.

```sh
uv run --locked python -m benefitfinder.jobs ai-catalog validate --manifest data/local/catalog/ai-batch.json
uv run --locked python -m benefitfinder.jobs ai-catalog publish --manifest data/local/catalog/ai-batch.json
# 해당 배치의 게시 결과가 잘못된 경우에만 같은 manifest 사용
uv run --locked python -m benefitfinder.jobs ai-catalog retire --manifest data/local/catalog/ai-batch.json
```

게시·철회는 `POLICY_PUBLISH_DATABASE_URL` 환경 변수로 별도 게시 역할을 받는다. 전체 배치는 한 트랜잭션이며 하나라도 실패하면 모두 롤백된다. 이미 게시된 정책 ID는 보호를 위해 AI 배치에서 거부한다. 철회는 manifest가 지정한 AI 게시 버전과 해시가 모두 현재 값과 일치할 때만 처리하며 다른 정책이나 사람 승인 정책을 건드리지 않는다. 이미 원천 변경으로 철회된 항목이 포함돼도 전체 요청은 실패하므로 상태를 먼저 확인한다. 최초 AI 배치는 기존 게시 버전을 교체하지 않으므로 철회 시 복원할 이전 버전은 없다.

## 계산과 남은 범위

모집 중이며 미충족이 없고 금액·중복·시점 검수가 끝난 정책만 조합에 들어간다. 기간별 금액은 검수한 지급·적립 일정으로 계산한다. 모집 종료·미충족·아직 조합 계산 전 목록, 개별 목록, 기회비용 근거를 분리한다. 근거 없는 정책 쌍은 동시 수급 확인 필요로 표시한다. 상황 변화 전후는 같은 게시 스냅샷을 쓰고 소득 절벽은 게시된 상한으로 비교한다.

만기형은 `benefit_amount.type: maturity`, `pay_timing: at_maturity`, `period_months`의 만기 달에 전액 한 번 지급하는 `payments`와 별도의 `maturity_accruals`·근거가 모두 있어야 검수 완료로 게시할 수 있다. `amount_won`·`max_total_won`·지급 총액·적립 증가분 총액이 같아야 한다. 적립 월은 1~만기 달 안에서 중복되지 않아야 하며 불균등 일정도 허용한다. 총액을 기간으로 나눠 일정을 추정하지 않는다. 중도 해지·조건부 매칭·본인 납입금 등이 분리되지 않으면 검수 대기로 남긴다. 새 필드를 생략한 기존 입력은 직렬화·승인 해시가 유지된다.

명목 1년·2년은 기간까지 적립된 지원금을 계산한다. 만기 전에는 현금 수령 예정액 0원과 미지급 예상 적립액을 `amount_breakdown`으로 분리한다. 만기가 기간 안에 있으면 현금 전액과 예상 적립액 0원이다. 현재가치 순위·합계는 선택 기간 안의 실제 지급만 할인하며, 만기 전 적립액은 별도 표시하고 할인하지 않는다. 상황 변화의 연간 지원금·실효비율은 같은 1년 명목 적립 기준을 사용하며 실제 연간 현금 수령액으로 표현하지 않는다.

6개 보기와 상황 변화 전후는 45초 계산 예산을 공유한다. 후보 128개·탐색 250,000 노드·안전 정수 한도를 넘으면 `503 calculation_limit_exceeded`다. 후보를 잘라 성공으로 반환하지 않는다. 기본 가상 계산기도 시간 제한과 정확한 그래프 탐색을 사용한다.

[ECOS 금리 저장소](ECOS_RATES.md)를 연결하면 같은 마지막 성공값으로 추천·상황 변화의 현재가치를 계산한다. 미설정·빈 금리 테이블·조회 실패 시 `discount_rate: null`이고 현재가치 보기는 계산 불가다. 가상 금리를 실제 금리로 사용하지 않는다. [서비스·단가 카탈로그](SERVICE_CATALOG.md)는 같은 DB에서 별도 승인·조회하고 현금 계산과 분리한다. 실제 서비스 자료와 단가 검수는 후속이다. [0010](F12_CATALOG.md)의 `explanation_clauses`를 포함하면 같은 승인 해시·버전으로 F13을 함께 게시한다. 같은 ID/버전의 게시 조항이 없으면 기본 문구다. 사용자 프로필·특수 조건·부모 정보는 모델에 전달하지 않는다.

실제 정책 30개 검수·게시와 정책 사실 검증은 코드 통과만으로 완료되지 않는다. [통합 검사](../../../../integration/README.md)는 PostgreSQL 17에 가상 자료만 넣어 게시·조회·교체·롤백·검수 필터·권한·장애·API 연결을 확인한다.

### 미검수 게시 배치 (0014)

미검수 게시 경로는 검수 판정을 만들지 않는다. `review_type`과 `review_status`는 `unreviewed`, 검수자와 검수 시각은 NULL이며, 원천 버전과 본문·레코드 해시를 별도로 기록한다. 현금 정책은 `reviewed=false`, 지급 일정 미확정, 중복 규칙 미확정인 경우만 허용하여 조합 계산에서 제외한다. 단가가 있는 서비스, F12 검수 연결, 가상 정책은 이 경로에서 게시하지 않는다. 기존 원천 연결·해시 및 수집 변경 시 철회 검사는 유지한다. 이 경로의 설명 조항은 검수된 F13 조항으로 게시하지 않는다.

각 manifest 항목은 `input`과 `source` 경로만 가진다. 게시 및 철회는 최대 50건씩 트랜잭션으로 처리하며 기존 게시 정책을 교체하지 않는다.

```sh
uv run --locked python -m benefitfinder.jobs unreviewed-catalog validate --manifest data/local/catalog/unreviewed.json
uv run --locked python -m benefitfinder.jobs unreviewed-catalog publish --manifest data/local/catalog/unreviewed.json
uv run --locked python -m benefitfinder.jobs unreviewed-catalog retire --manifest data/local/catalog/unreviewed.json
```

게시·철회에는 `POLICY_PUBLISH_DATABASE_URL`이 필요하다. 신규 스키마를 적용한 뒤 기존 서버는 미검수 항목을 읽지 않으므로 변경된 서버 코드도 배포해야 추천·상세에서 조회된다. 운영 스키마 적용과 서버 배포는 정해진 승인·변경 절차를 따른다.
