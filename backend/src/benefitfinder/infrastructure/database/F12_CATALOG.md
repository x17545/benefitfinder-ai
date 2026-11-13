# F12 사람 검수에서 카탈로그 게시까지

`catalog-from-f12`는 원본 검수 쌍·최신 사람 판단·승인 내보내기와 **사람이 작성한 실행 규칙**을 연결한다. DB·LLM 호출이나 승인 파일 생성은 없다. 실제 자료가 없으면 가상 회귀만 실행할 수 있다.

## 입력 준비

- `--pair-dir`: 원본 `rules-pending.json`, `facts-pending.json` 디렉터리. 큐의 `reviews/d01` 등도 같은 형식이다.
- `--decisions`: 해당 쌍 해시에 연결된 최신 전체 사람 판단 이력.
- `--approved`: 같은 이력의 `rule-review export-approved`. 원본과 이력에서 재구성한 내용과 정확히 일치해야 한다.
- `--input`: 사람이 작성한 [현금](POLICY_CATALOG.md) 또는 [서비스](SERVICE_CATALOG.md) 입력. 연산자·금액·지급 일정·수량·단가를 확인한다. 명령이 `benefit.reviewed`를 바꾸지 않는다.
- `--source`: 쌍의 UTF-8 전문과 정확히 같은 파일. 정책 ID·분류·URL·버전·확인 시각·전문 해시도 입력과 일치해야 한다.
- `--plan`: 아래 대응표. 별도 단가가 있는 서비스는 `--price-source`도 전달한다.

```json
{
  "format_version": 1,
  "conditions": [{"item_id": "eligibility:1", "condition_id": "age"}],
  "payment_items": ["amount:1", "timing:1"],
  "conflicts": [],
  "explanations": [{"item_id": "eligibility:1", "citation_index": 0,
    "id": "age", "title": "지원 나이 근거", "topics": ["age"]}],
  "reference_only": []
}
```

항목 번호는 예시이며 실제 승인 항목으로 바꾸고 모든 키를 명시한다. 실행 규칙은 입력의 `conditions`, `payments`에 있다. 대응표가 연산자·개월 수를 추정하지 않는다. 모든 자격 조건·중복 제한은 승인 후보의 인용·위치에 대응해야 한다. 현금 지급 근거는 승인 금액·시점 인용과 일치해야 하고 검수 완료 현금은 둘 다 필요하다. 기본 금액도 후보의 명시 수치와 일치해야 한다. 잘못된 후보는 반려·수정한 새 쌍으로 검수한다.

만기형은 사람이 별도로 해석한 `maturity_accruals`와 근거를 입력한다. 적립 근거 `maturity_accrual_evidence`도 `payment_items`로 대응한 승인 금액·시점 인용에 포함돼야 한다. 적립 증가분·만기 총액·본인 납입금 제외 여부는 최종 전체 입력 검수에서 확인하며, 이 명령이 균등 적립을 추정하거나 승인 범위를 넓히지 않는다.

`conflicts`는 `{item_id, policy_id}`이며 조건·예외·기간·차감·미해결 항목이 없는 단일 대상의 양방향 동시 제외만 지원한다. 복합 규칙을 단순 제외로 바꾸지 않는다. `reference_only`는 `{item_id, reason}`으로 계산에 쓰지 않는 승인 항목의 사유를 기록한다. 모든 승인 항목을 실행 대응 또는 참조 사유로 설명해야 한다. 설명에 선택한 사실만으로 계산 검수가 끝나지 않는다. 미판단 후보나 실행하지 못한 승인 자격·중복 규칙이 남으면 현금은 `reviewed: false`, `unresolved_combination_rules: true`가 필요하고 서비스 준비는 거부한다.

F13은 선택한 승인 항목의 원문을 그대로 복사한다. `citation_index`는 0부터 시작하며 일반 사실은 0이다. 토픽은 `age`, `region`, `employment`, `housing`, `graduation`, `amount`, `application`만 허용한다. 신청 안내·서비스 내용·별도 단가·모집 상태 등 후보가 표현하지 않는 항목은 전체 입력의 별도 사람 검수 범위다.

## 준비·최종 승인·게시

```sh
uv run --locked python -m benefitfinder.jobs catalog-from-f12 \
  --pair-dir data/local/review/pair --decisions data/local/review/decisions.json \
  --approved data/local/review/approved.json --input data/local/catalog/interpreted.json \
  --plan data/local/catalog/plan.json --source data/local/catalog/source.txt \
  --output data/local/catalog/prepared.json
```

새 파일에만 0600 권한으로 작성한다. `f12_review`의 쌍·판단 이력·내보내기·대응표 정규 JSON 해시, 원문 ID·마지막 검수 시각과 `explanation_clauses`가 전체 `record_sha256`에 포함된다. 다중 문서 근거를 한 출처로 합치지 않는다. 큐의 완료는 승인이 아니며 작업 manifest 인증 도구도 아니다.

검수자가 원문 전체·누락·조건·지급·중복·안내·설명을 확인한 뒤 [별도 승인 파일](POLICY_CATALOG.md#입력과-검수)을 작성한다. 최종 게시 검수 시각은 원문 확인과 마지막 F12 판단 이후여야 한다. 해시 출력은 승인이 아니며 승인 파일은 검수자 인증을 대신하지 않는다. `policy-catalog publish` 또는 `service-catalog publish`로 명시적으로 게시한다. 실제 정책은 검수한 `collection_sources`가 필요하다. 준비 후 원천이 바뀌면 게시 트랜잭션의 현재 해시·변경 상태 검사에서 거부된다.

## 같은 버전의 F13과 권한

관리자가 0009 이후 `0010_catalog_explanation_publication.sql`을 명시적으로 적용한다. 앱 시작 시 DDL은 없다. 카탈로그 게시 시 제한된 소유자 트리거가 같은 트랜잭션에서 이전 근거를 은퇴시키고 모든 조항을 만든다. 게시자는 기존 카탈로그 INSERT·게시 상태 UPDATE만 사용하며 F13 직접 수정 권한은 필요 없다. 하나라도 실패하면 전체가 롤백된다.

문서·조항을 승인 해시·검수자·검수 시각·게시 시각에 연결하고 인용·연결 해시의 직접 변경을 거부한다. 조회도 전체 승인 해시와 조항을 다시 검사한다. 두 분야 정책은 두 분류로 조회할 수 있다. 카탈로그 은퇴·원천 변경·누락 시 연결된 F13도 내리며 재등장으로 자동 복구하지 않는다.

기존 `(정책 ID, 문서 버전)`은 덮어쓰지 않는다. 같은 버전 재게시·기존 F13 초안과 키 충돌은 실패하고 이전 게시를 보존한다. 같은 공고의 승인 개정을 별도로 관리하는 모델은 지원하지 않으므로 사실과 무관한 버전을 자동 생성하지 않는다. 연결 없는 기존 가상/레거시 F13과 조항 없는 카탈로그의 승인 해시는 유지한다.

F13 런타임에는 `f13_policy_documents`, `f13_clauses`, **`policy_catalog_releases` SELECT**가 필요하다. 사용자 프로필은 작업·검색·모델에 전달하지 않는다. [PostgreSQL 회귀](../../../../integration/README.md)는 가상 자료만 사용한다. 실제 자료 검수·정책 사실 검증·운영 DB 적용은 별도다.
