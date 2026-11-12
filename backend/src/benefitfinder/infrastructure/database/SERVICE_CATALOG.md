# 게시 서비스·단가 카탈로그 (F14)

서비스도 `public.policy_catalog_releases`의 검수·게시 버전과 원천 연결을 사용한다. 내부 입력의 `kind: "service"`로 현금과 구분하며, 현금 지급 일정이나 조합 후보에 넣지 않는다. 공개 형식은 기존 `contracts/v1/service.schema.json`과 서비스형 benefit 계약을 유지한다. 취업·주거는 계속 `youth` 안의 분류다.

## 검수 입력

서비스 내용·횟수는 공식 공고에서 확인하고, 단가는 근거 있는 시중 가격으로 사람이 정한다. F12 추가 근거 파일은 검수 자료이며 이 입력이나 승인을 자동으로 만들지 않는다. 실제 자료는 Git 제외 `data/`에 둔다. 공유 fixture는 가상 자료다.

`ServiceCatalogRecord`는 다음 필드만 받는다.

| 필드 | 내용 |
| --- | --- |
| `kind` | `service` |
| `benefit` | 기존 정책 ID·이름·요약·기관·취업/주거 분류·모집 일정·자격 조건/근거·출처·가상 여부·중복 제한. `service`에는 `item`, `unit`, 정수 `quantity`만 저장 |
| `conditions` | 자격 조건 ID와 정확히 대응하는 검수 실행 규칙. 현금과 같은 나이/선택값/소득 구간 판정이며 미지원 조건은 `manual` |
| `service_evidence` | 품목·횟수에 관한 공식 원문 인용 1개 이상. 공고의 URL·버전·확인 시각과 일치 |
| `price` | 미확인이면 `null`. 확인했으면 정수 `unit_value_won`, `basis`, 별도 가격 문서 `source`, `quote`, `location`, 가격 원문 `source_sha256` |
| `source_sha256` | 정확한 공식 공고 텍스트의 UTF-8 SHA256 |
| `collection_sources` | 실제 자료는 1~20개 현재 원천 ID·내용 해시 연결 필수. 형식은 현금 게시 입력과 동일 |

가격 문서의 `source`는 HTTPS URL·문서 버전·시간대 있는 확인 시각이다. 가격이 있으면 별도 가격 원문 파일과 해시·인용을 검증한다. 공고와 가격이 같은 문서라면 두 인자로 같은 파일을 줄 수 있다. 단가·횟수·출처·확인일·규칙·인용·원천 연결을 포함한 전체 입력의 해시를 사람이 승인한다. 승인 시각은 공고와 가격 확인 이후여야 한다. 추정 환산액·현금 지급 일정·LLM이 정한 승인 필드를 입력으로 받지 않는다.

## 마이그레이션·권한

관리자가 0001·0002·0005·0006 다음 `backend/migrations/versions/0008_service_catalog.sql`을 명시적으로 적용한다. F12의 0007과는 독립적이며, 자동 DDL·기본 단가·실제 정책 삽입은 없다. 운영·공유 DB 적용은 별도 승인 작업이다.

0008은 기존 현금 자료를 유지하고 서비스와 현금의 입력 형태가 섞이지 않게 한다. 같은 정책 ID를 과거 현금 버전에서 서비스 버전으로 바꾸는 것도 차단한다. 하나의 정책에 둘 다 있는 경우 담당자가 공고·정책 ID 매핑을 검토해 별도 혜택 ID로 준비해야 한다. 입력 오류를 피하려고 임의로 새 정책을 만들어내지 않는다.

런타임은 기존 `POLICY_DATABASE_URL`의 SELECT 전용 역할로 두 종류를 읽는다. 별도 서비스 DB나 서버를 만들지 않는다.

```sql
GRANT USAGE ON SCHEMA public TO benefitfinder_reader;
GRANT SELECT ON public.policy_catalog_releases TO benefitfinder_reader;
```

게시자는 기존 `POLICY_PUBLISH_DATABASE_URL`과 게시 권한을 사용한다. [현금 게시 권한](POLICY_CATALOG.md)·[원천 연결](POLICY_SOURCE_RECHECKS.md)을 따른다. 서비스·현금 모두 원천 잠금 → 정책 잠금 → 이전 버전 퇴역/새 버전 삽입 순서를 지킨다. 실패하면 이전 게시 상태까지 롤백한다. 원천 변경·누락은 같은 트랜잭션에서 서비스를 내리고 재검수 이력을 남긴다. 내용이 같은 재수집은 승인을 유지하고 재등장은 자동 승인이 아니다.

## 검증과 명시적 게시

```sh
uv run --locked python -m benefitfinder.jobs service-catalog validate \
  --input data/local/service.json --source data/local/notice.txt \
  --price-source data/local/market-price.txt
```

단가를 모르면 입력의 `price`를 null로 두고 `--price-source`를 생략한다. 검증은 해시·정책 ID·문서 버전만 출력한다. 공고나 가격 원문을 출력하지 않는다. 사람이 검토한 뒤 `record_sha256`, `reviewed_by`, 시간대 있는 `reviewed_at` 세 필드의 별도 승인 파일을 준비한다. 도구가 사람 승인을 대신 생성하지 않는다.

```sh
uv run --locked python -m benefitfinder.jobs service-catalog publish \
  --input data/local/service.json --source data/local/notice.txt \
  --price-source data/local/market-price.txt --approval data/local/approval.json
```

단가만 달라져도 전체 승인 해시가 바뀌므로 새 문서 버전과 새 검수가 필요하다. 같은 버전 파일을 덮어쓰지 않는다. 가격 웹페이지의 변경을 자동 수집·탐지하는 기능은 없으며 가격 확인과 갱신은 사람의 자료 준비 단계다. 공유 fixture와 테스트의 가상 승인값을 실제 검수 기록으로 쓰지 않는다.

## API 동작과 한계

`POLICY_DATABASE_URL`이 있으면 추천은 검수·게시된 현금과 서비스를 각각 조회한다. 서비스는 코드 자격 판정 후 모집 중인 대상만 S6에 표시하고, 근거 있는 단가 × 횟수만 합산한다. 단가 미확인·미래 확인일·안전한 정수 범위 초과는 단가와 환산 근거/금액을 모두 null로 둔다. 현금의 6개 기간·현재가치·상황 변화 합계에는 더하지 않는다. 가상 서비스가 결과에 들어가면 전체 추천의 가상 자료 표시를 유지한다.

공개 상세는 프로필 없이 같은 정책 ID·버전·출처·자격 근거·서비스 내용·중복 제한을 반환한다. 마감 서비스는 추천에서 빠지지만 공개 상세에는 마감 상태를 표시한다. 미검수·퇴역 버전은 공개하지 않는다. 조회 실패나 승인 해시 불일치는 503이며 가상 예제로 대체하지 않는다. 한 번에 5,000개를 넘으면 계산/목록을 잘라 성공으로 표시하지 않고 오류로 반환한다.

서버를 재시작하지 않아도 새 게시·퇴역 버전을 읽으며 `GET /api/v1/meta`는 현금·서비스를 함께 확인한 최근 게시 시각을 반환한다. 둘 다 비어 있으면 시각을 추정하지 않는다. DB·LLM 연결 없이 실행한 개발 예제와 구분한다. 실제 사람이 검수한 서비스·단가 자료, 운영 게시, 원문/가격 갱신 담당의 검수 인계는 아직 별도 준비가 필요하다.
