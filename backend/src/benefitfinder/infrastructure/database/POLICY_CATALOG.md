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

## 계산과 남은 범위

모집 중이며 미충족이 없고 금액·중복·시점 검수가 끝난 정책만 조합에 들어간다. 기간별 금액은 지급 일정으로 계산한다. 모집 종료·미충족·아직 조합 계산 전 목록, 개별 목록, 기회비용 근거를 분리한다. 근거 없는 정책 쌍은 동시 수급 확인 필요로 표시한다. 상황 변화 전후는 같은 게시 스냅샷을 쓰고 소득 절벽은 게시된 상한으로 비교한다.

6개 보기와 상황 변화 전후는 45초 계산 예산을 공유한다. 후보 128개·탐색 250,000 노드·안전 정수 한도를 넘으면 `503 calculation_limit_exceeded`다. 후보를 잘라 성공으로 반환하지 않는다. 기본 가상 계산기도 시간 제한과 정확한 그래프 탐색을 사용한다.

[ECOS 금리 저장소](ECOS_RATES.md)를 연결하면 같은 마지막 성공값으로 추천·상황 변화의 현재가치를 계산한다. 미설정·빈 금리 테이블·조회 실패 시 `discount_rate: null`이고 현재가치 보기는 계산 불가다. 가상 금리를 실제 금리로 사용하지 않는다. 만기 전 예상 적립액은 별도 모델이 필요하므로 만기형은 검수 완료 계산 대상으로 게시하지 못한다. [서비스·단가 카탈로그](SERVICE_CATALOG.md)는 같은 DB에서 별도 승인·조회하고 현금 계산과 분리한다. 실제 서비스 자료와 단가 검수는 후속이다. [0010](F12_CATALOG.md)의 `explanation_clauses`를 포함하면 같은 승인 해시·버전으로 F13을 함께 게시한다. 같은 ID/버전의 게시 조항이 없으면 기본 문구다. 사용자 프로필·특수 조건·부모 정보는 모델에 전달하지 않는다.

실제 정책 30개 검수·게시와 정책 사실 검증은 코드 통과만으로 완료되지 않는다. [통합 검사](../../../../integration/README.md)는 PostgreSQL 17에 가상 자료만 넣어 게시·조회·교체·롤백·검수 필터·권한·장애·API 연결을 확인한다.
