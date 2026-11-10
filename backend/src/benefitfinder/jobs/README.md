# 작업 실행

현재 구현: `collect-youthcenter`는 [수동 수집](../ingestion/README.md#로컬-수집-실행)을, `refresh-youthcenter`는 [이전 성공본을 보존하는 F08 갱신](#f08-온통청년-갱신-한-번-실행)을 실행한다. F08은 선택적으로 디스코드 결과 알림을 보내고 macOS의 `launchd`로 매일 실행할 수 있다. `rule-review`는 로컬 검수 자료의 준비·명시적 승인·재검수·상태 조회를 실행한다.

## 한 번에 F12 검수 자료 만들기

공식 공고 한 건, 두 정책의 ID와 빈 후보 목록을 담은 검수 입력을 준비한다. 아래 이름과 ID는 가상이다. Ollama에서 도구 호출을 지원하는 로컬 모델을 실행한 뒤 저장소 루트에서 실행한다.

```bash
uv run --locked python -m benefitfinder.jobs rule-review extract-all \
  --input data/processed/rule-reviews/notice-input.json \
  --target-name '가상 정책 B' --target-alias '가상 B' --target-id youth:virtual:b \
  --model gemma4:e4b --output-dir data/processed/rule-reviews/notice-review
```

같은 원문으로 중복 제한과 자격·금액·지급 시점을 각각 추출한 뒤 새 `--output-dir`에 `rules-pending.json`과 `facts-pending.json`을 저장한다. 한쪽 추출이 실패하면 결과를 저장하지 않으며 기존 디렉터리는 덮어쓰지 않는다. 원문과 두 결과 모두 Git 제외 경로에 둔다.

## 두 F12 검수 파일을 함께 보기

`extract-all`에 넘긴 결과 디렉터리를 지정한다.

```bash
uv run --locked python -m benefitfinder.jobs rule-review show-all \
  --input-dir data/processed/rule-reviews/notice-review
```

두 파일의 공고 버전·해시와 인용을 저장된 원문으로 확인한 뒤 중복 제한·예외, 자격 조건, 지원 금액, 지급 시점을 검수 대기 상태와 함께 보여 준다. 줄 번호가 있는 인용은 해당 위치의 원문과 정확히 일치해야 한다. 공식 출처와 인용 위치를 표시하며, 추출된 근거가 없으면 `not_found`, 고정 월 지급일이 확인되지 않으면 `미확인`으로 표시한다. 파일이 빠졌거나 서로 다른 공고의 파일을 섞으면 내용을 출력하기 전에 실패한다. 조회는 읽기 전용이며 모델이나 공식 사이트를 호출하지 않는다. 중복 제한 상태 요약만 보려면 기존 `rule-review show --snapshot <경로>`를 사용한다.

### 항목별로 승인·반려 사유 남기기

`show-all`에 표시된 항목 ID를 고른다. `rule`은 예외를 포함한 중복 제한 후보, `eligibility`는 자격 근거, `amount`는 금액과 근거, `timing`은 지급 시점 근거다. `not_found`에는 검수할 후보가 없어 ID를 부여하지 않는다. `decide`에는 승인(`approve`) 또는 반려(`reject`), 검수자, 사유를 모두 명시한다. 아래 값은 가상 예시다.

```bash
review_dir=data/processed/rule-reviews/notice-review
uv run --locked python -m benefitfinder.jobs rule-review decide \
  --input-dir "$review_dir" --item-id rule:1 --decision approve \
  --reviewer virtual-reviewer --reason '원문과 제한 조건·예외가 일치함' \
  --output "$review_dir/decisions-1.json"
uv run --locked python -m benefitfinder.jobs rule-review decide \
  --input-dir "$review_dir" --item-id amount:1 --decision reject \
  --reviewer virtual-reviewer --reason '지급 한도를 함께 확인해야 함' \
  --decisions "$review_dir/decisions-1.json" --output "$review_dir/decisions-2.json"
uv run --locked python -m benefitfinder.jobs rule-review show-all \
  --input-dir "$review_dir" --decisions "$review_dir/decisions-2.json"
```

이전 기록을 `--decisions`로 넘기면 새 판단을 덧붙인 파일을 만든다. 같은 항목을 다시 판단해도 이력은 남고, 조회에는 마지막 판단과 사유를 표시한다. 판단이 없는 항목은 `needs_review`로 표시된다. 두 입력 파일이나 이전 기록을 덮어쓰지 않으며 원문 또는 후보가 바뀐 다른 결과에 기존 판단을 적용하지 않는다. 이 기록은 로컬 후보 검수용이며 `approve --snapshot`의 규칙 승인 기록이나 운영 게시에 연결되지 않는다. 원문·후보·판단 기록은 모두 Git 제외 경로에 둔다.

### 승인 항목만 로컬 파일로 내보내기

같은 두 후보 파일과 최신 판단 기록을 지정한다.

```bash
uv run --locked python -m benefitfinder.jobs rule-review export-approved \
  --input-dir "$review_dir" --decisions "$review_dir/decisions-2.json" \
  --output "$review_dir/approved-export.json"
```

가장 최근 판단이 승인인 항목만 새 JSON에 저장한다. 각 항목에 검수자·시각·사유, 원문 인용과 위치를 남기고, 공고의 출처·버전·해시를 함께 담는다. 반려와 검수 대기 항목, 공고 전문은 포함하지 않는다. 승인 후 반려한 항목도 제외한다. 승인 항목이 없으면 `f12_no_approved_items`로 실패하고 파일을 만들지 않는다. 기존 자료를 덮어쓰거나 새 승인을 생성하지 않는다. 이 파일은 Git 제외 경로에 두는 로컬 검수 산출물이며 운영 게시에 연결되지 않는다.

## 로컬 모델로 후보 만들기

`rule-review extract`는 이미 확보한 공식 공고 텍스트 한 건과 상대 정책 한 건으로 중복 제한 후보 하나를 만든다. 검수 입력 v1과 같은 형식으로 공식 `documents` 한 건, 두 정책의 `policies`, 빈 `candidates`를 준비한다. `rule-review example`의 가상 출력에서 필드 구조를 확인할 수 있다. 두 정책 모두 청년 취업·주거 범위에 있어야 하고, 상대 정책의 이름과 ID는 입력자가 공식 자료로 확인한다. 공고가 같은 정책을 다른 이름으로 부르면 확인된 표기를 `--target-alias`로 반복해서 넘길 수 있다. 모르는 별칭을 유사성만으로 정책에 연결하지 않는다. 아래 이름·ID는 가상이다.

```bash
ollama pull gemma4:e4b
uv run --locked python -m benefitfinder.jobs rule-review extract \
  --input data/processed/rule-reviews/notice-input.json \
  --target-name '가상 정책 B' --target-alias '가상 B' --target-id virtual-b --model gemma4:e4b \
  --output data/processed/rule-reviews/notice-candidates.json
uv run --locked python -m benefitfinder.jobs rule-review prepare \
  --input data/processed/rule-reviews/notice-candidates.json \
  --output data/processed/rule-reviews/notice-pending.json
```

Ollama가 `127.0.0.1:11434`에서 실행 중이고 도구 호출을 지원하는 로컬 모델이 설치되어 있어야 한다. 모델은 검색된 줄과 앞뒤 문맥을 함께 보고, 여러 줄에 걸친 제한·기간·예외를 연속된 원문 범위로 인용할 수 있다. 선택한 근거의 명시적 허용은 제한 조건과 구분해 `exceptions`에 저장한다. 예외가 조건에 섞이거나 선택한 근거에서 빠지면 모델에 재제출을 요청한다. 콘솔에는 모델·도구 순서·소요 시간만 남긴다. 모델이 낸 해석은 사람이 검수하며, 이 명령은 승인하거나 게시하지 않는다. 실제 원문과 후보는 `data/` 같은 Git 제외 경로에 보관한다.

## 자격·금액·지급 시점 후보 만들기

공식 공고 한 건을 담은 기존 검수 입력으로 실행한다. 상대 정책 이름과 ID는 필요 없다.

```bash
uv run --locked python -m benefitfinder.jobs rule-review extract-facts \
  --input data/processed/rule-reviews/notice-input.json --model gemma4:e4b \
  --output data/processed/rule-reviews/notice-facts-pending.json
```

모델은 자격 조건·지원 금액·지급 시점에 해당하는 원문 줄을 고른다. 결과는 원문 인용, 줄 번호, 문서 버전·해시가 붙은 별도 `needs_review` 파일이다. 금액 숫자는 인용에 직접 적힌 원화 표기만 옮긴다. 월 금액과 기간을 곱해 총액을 만들거나, 지급 시점의 상대 조건을 고정 지급일로 바꾸지 않는다. 근거를 찾지 못한 항목은 `not_found`다. 날짜 범위·선택지·기한처럼 고정 월 지급일을 하나로 확정할 수 없으면 인용을 보존하고 `scheduled_monthly_day: null`로 둔다. `not_found`는 후보에서 근거를 찾지 못했다는 뜻이며 원문 전체에 조건이 없다는 판정이 아니다. 이 파일은 아래 중복 제한 검수 명령의 승인·게시 입력이 아니다.

자격 조항은 고정된 앞뒤 줄 수로 자르지 않고 이어진 조건·예외까지 모델에 보여 준다. 소득 조건의 별도 예외와 증빙 요건, 사업자 제외 다음 줄의 휴·폐업 예외도 원문 인용에 남긴다. 금액은 월 한도뿐 아니라 실제 비용·생애 한도·기수혜 차감 및 별도 절의 급여 차감 FAQ를 함께 제시한다. 금액 수치가 없는 차감 근거도 보존하되 `amounts_won`은 빈 배열이다. 지원금으로 소득 한도를 옮기거나, 원문에 없는 총액을 계산하지 않는다. 문맥 한도(240줄·35,000자·한 줄 700자)를 넘으면 `policy_facts_context_limit`로 실패하며 일부 조건을 조용히 생략하지 않는다. 모호한 접속어·출생연도·가구 구분은 원문 그대로 검수 대기에 둔다.

보완 추출은 새 결과 파일에 저장한다. `show-all`로 새 두 파일을 확인하되 이전 `--decisions`를 연결하지 않는다. 인용 범위나 후보가 달라지면 항목 번호도 바뀔 수 있으며, 기존 판단은 다른 후보 쌍에 재사용할 수 없다. 근거 보완 자체는 승인·DB 적재·게시를 수행하지 않는다.

규칙 후보가 없는 공식 보충 지침도 `facts`만으로 검수할 수 있다. 중복 제한 근거가 없어 `rules-pending.json`의 `reviews`가 비어도 자격·금액·지급 시점 중 하나라도 `extracted`면 두 파일을 함께 저장·조회하고 항목별 판단·승인 내보내기를 진행한다. 모두 `not_found`면 저장·조회 모두 실패한다. 같은 정책의 다른 공식 문서는 기존 공고의 근거에 덧붙이지 않는다. 원문 URL·버전·해시를 별도 파일로 보존하고 해당 문서의 인용으로만 검수한다.

## 로컬 규칙 검수 실행

다음 예제는 실제 정책과 무관한 가상 자료이며 DB·LLM·네트워크 없이 실행한다. 저장소 루트에서 실행하고, 기존 파일이 있으면 새 경로를 사용한다.

```bash
uv run --locked python -m benefitfinder.jobs rule-review example --output data/processed/rule-reviews/example-input.json
uv run --locked python -m benefitfinder.jobs rule-review prepare --input data/processed/rule-reviews/example-input.json --output data/processed/rule-reviews/example-pending.json
uv run --locked python -m benefitfinder.jobs rule-review show --snapshot data/processed/rule-reviews/example-pending.json
```

원문·전체 지침·예외를 사람이 읽고 후보가 이를 정확히 나타내는지 확인한 뒤 승인 기록을 만든다. 아래 `virtual-reviewer`는 가상 검수자다. 실제 작업에서는 담당자의 식별자를 넣는다.

```bash
uv run --locked python -m benefitfinder.jobs rule-review approve --snapshot data/processed/rule-reviews/example-pending.json --candidate-id virtual-a-b --reviewer virtual-reviewer --output data/processed/rule-reviews/example-approved.json
```

다시 수집·분석한 **전체 문서·현재 정책 목록·전체 후보**를 담은 입력 파일이 생기면 기존 기록을 재검사한다. 다음 명령의 `current-input.json`은 수집·추출 담당이 준비할 파일이며 자동 생성되지 않는다.

```bash
uv run --locked python -m benefitfinder.jobs rule-review refresh --snapshot data/processed/rule-reviews/example-approved.json --input data/processed/rule-reviews/current-input.json --output data/processed/rule-reviews/example-rechecked.json
```

| 명령 | 결과 |
|---|---|
| `example --output` | `[가상]` 문서·정책·후보의 입력 JSON 생성. 실제 수집·모델 호출 없음 |
| `prepare --input --output` | 입력 후보의 근거·범위·배치를 검사하고 모두 검수 대기로 준비 |
| `approve --snapshot --candidate-id --reviewer --output` | 저장된 전체 입력으로 재검사한 뒤 특정 후보의 사람 승인 기록 생성 |
| `refresh --snapshot --input --output` | 새 전체 입력으로 기존 승인을 해제하거나 유지. 기존 사유·검토한 후보는 보존 |
| `show --snapshot` | 저장된 입력으로 상태를 다시 검사해 요약. 파일을 수정하거나 공식 사이트를 조회하지 않음 |

파일 출력은 새 파일로만 만든다. 승인·재검수 후에도 이전 파일의 원문·검수자·시각은 그대로 남으며, 실패한 명령이 기존 자료를 바꾸지 않는다. 종료 코드 `0`은 명령 성공(검수 대기 결과도 포함), `1`은 형식·검수·파일 오류, `2`는 잘못된 명령 인자다. 오류는 원문·인용문·외부 예외 본문 없이 고정 코드로 표시한다. 인용은 검수용 상세 조회인 `show-all`이 성공했을 때 표시한다.

`created_at`은 결과 파일 생성 시각이며 공고의 마지막 확인일이나 검수 시각을 대신하지 않는다. 저장된 수집 시각·검수 시각이 파일 생성 시각보다 뒤이면 조회·승인·재검수는 고정 오류 코드로 실패하고 새 파일을 만들지 않는다. 재검수는 입력을 교체하기 전에 기존 파일도 검사하며, 여기서 발견된 승인 불일치·상충 사유를 새 입력에도 보존한다. 원문이 동일한 재수집은 기존 승인 시각을 유지한다. 원문 변경·삭제·공식 출처 미확인·정책 범위 변경·상충 후보 추가는 검수 대기로 돌아간다. 재검수는 새 후보를 자동으로 승인하거나 기존 사유를 지우지 않는다. 새 추출 후보와 해결한 검수 사유는 별도 `prepare` 파일에서 다시 검토한다. 입력 누락을 새 수급 허용으로 해석하지 않는다.

이 형식은 `RuleReviewBatch`·`RuleReviewSnapshot`의 **로컬 개발용 직렬화 v1**이며 공유 API·게시 계약·PostgreSQL 스키마가 아니다. 입력 파일은 `format_version: 1`, `documents`, `policies`, `candidates`를 담는다. 결과는 `operation`, `created_at`, 전체 `batch`와 `reviews`를 담고 `status`는 저장하지 않는다. 날짜에는 시간대가 필요하며 알 수 없는 필드·중복 JSON 키·잘못된 타입/버전은 거부한다. 정확한 입력 모양은 `example`이 만든 파일과 `application/policy_rules.py`를 본다.

실제 자료는 Git 제외 경로에 보관하고 기존 수집 결과 `records.json`을 그대로 이 입력으로 사용하지 않는다. 공식 원문과 분류의 확인, 텍스트 추출·후보 생성은 별도 준비가 필요하다. 승인 전 최신 전체 입력으로 재검수하고, 검토 중인 JSON 안의 원문·근거 위치·미해결 사유를 함께 확인한다. 로컬 승인 기록은 검수자 인증이나 운영 게시를 대신하지 않으며 공유·게시 연결은 관련 계약과 함께 검토한다.

## F12 추출 품질 평가

`evaluate-f12`는 F12 로컬 v1 검수 자료와 사람이 승인한 내보내기를 **읽기 전용**으로 비교한다. 기존 추출·승인 코드를 가져오거나 실행하지 않으며 모델·네트워크·DB에 접근하지 않는다. 승인·반려·보류·게시 상태를 바꾸지 않는다. 실제 원문·기준·후보·평가 결과는 Git 제외 경로에 둔다. 공유 테스트는 가상 자료만 사용한다.

평가 manifest 예제는 가상 경로다. 경로는 manifest 파일의 디렉터리를 기준으로 해석하며 절대 경로도 허용한다.

```json
{
  "format_version": 1,
  "cases": [{
    "id": "virtual-employment",
    "category": "employment",
    "reference_dir": "gold/employment",
    "approved_exports": ["gold/employment/approved-export.json"],
    "candidate_dir": "candidates/employment"
  }]
}
```

- `reference_dir/rules-pending.json`은 공식 원문·정책·버전의 기준이다. `approved_exports`는 `f12_approved_review_export` v1이며 검수자·사유·시각을 포함한 승인 항목만 읽는다. 승인 기록의 진위·검수자 신원을 인증하는 도구는 아니므로 실제 사용자 검수 기록과 대조해 기준 파일을 선택한다. 대기/반려 파일을 정답으로 넣지 않는다.
- `candidate_dir`에는 평가할 `rules-pending.json`, `facts-pending.json`이 필요하다. 기준과 후보를 같은 디렉터리로 지정할 수 없다. 후보 번호·순서가 달라도 같은 분야/종류와 원문 범위로 비교한다. 규칙은 상대 정책·관계 종류·방향도 맞아야 한다.
- 승인 인용의 범위를 하나 또는 여러 유효한 후보 인용이 모두 포함하는지 검사한다. 인용 사이의 원문 공백만 빠진 경우는 허용하고, 조건·접속어·숫자 등 내용 누락은 부분/누락으로 보고한다. 금액은 연결된 후보 수치 집합도 대조한다.
- `exceptions`는 별도 필드의 텍스트와 그 후보가 실제 인용한 원문을 함께 검사한다. 공백만 정규화하고 표현의 의미가 같은지는 추정하지 않는다. 인용에 예외가 있어도 별도 필드가 비면 누락이며, 다른 표현은 사람 검토 대상이다. 자격/금액의 조건·예외 문맥은 승인한 인용 범위의 누락으로 평가한다.
- 후보의 인용문·위치·문서 ID/버전/해시를 원문과 대조한다. 위치는 `line N`, `lines N-M`, `PDF pN / text lines N-M`처럼 원문 텍스트의 줄 범위가 있어야 자동 검증된다. 범위 안에서도 반복 인용이 모호하면 오류로 보고한다. PDF 페이지 자체의 정확성은 텍스트 줄 검사와 별도다.
- 서로 다른 원문 버전은 비교 불가다. 추출 실패는 `candidate_dir: null`과 `candidate_error: "context_limit"`(또는 `model_failure`, `extraction_failed`)로 명시한다. 이 경우 후보를 만들거나 누락으로 단정하지 않는다. 모든 기준 근거가 평가되지 못하면 전체 `approved_evidence_recall`은 `null`이고, 비교 가능 근거의 recall과 전체 평가 가능 비율을 각각 제공한다.
- 검수 기준은 공고 전체 정답이 아닐 수 있다. 추가 후보/승인 범위 밖 인용은 `unscored_content`로 남기며 오답·승인으로 바꾸거나 정밀도를 추정하지 않는다. 원문 인용 일치도 정책 해석·수급 자격의 정확성을 보장하지 않는다.

```sh
uv run --locked python -m benefitfinder.jobs evaluate-f12 \
  --manifest data/local/f12-evaluation/manifest.json \
  --output data/local/f12-evaluation/report.json
```

기존 보고서는 덮어쓰지 않는다. 콘솔에는 집계와 고정 오류 코드만 표시하며 원문·검수자·파일 내용은 출력하지 않는다. JSON 보고서는 항목별 포함/부분/누락/비교 불가, 예외의 일치/누락/표현 변경/인용 미지원, 후보 인용 오류, 금액 수치 차이와 평가 한계를 담는다. `requires_human_review: true`, `approval_effect: none`은 항상 유지한다.

종료 코드 `0`은 지정된 승인 근거 범위에서 발견 사항이 없음, `1`은 누락·예외/근거/수치 차이·비교 불가 등 발견 사항이 있는 보고서 생성 완료, `2`는 입력/파일/출력 경로 오류다. `0`은 자동 승인이나 전체 공고의 정확성을 뜻하지 않는다. 문맥 한도를 높이거나 공고를 정답 구간만 잘라 점수를 맞추지 말고 실패를 별도 기록한다.

실제 자료 없이 회귀를 실행하려면:

```sh
uv run --locked python -m unittest backend.tests.test_f12_evaluation backend.tests.test_f12_evaluation_files backend.tests.test_f12_evaluation_cli -v
```

## 긴 공식 지침의 문맥 분할 추출

`extract-chunked-facts`는 공식 청년 취업·주거 지침 전체를 순서대로 로컬 `gemma4:e4b`로 읽고 자격·금액·지급 시점의 근거 후보를 검수 대기로 만듭니다. 온라인 추천 경로와 연결하지 않습니다. 모델·DB·계약 의존성을 추가하지 않으며 기존 F12 추출/평가 명령과는 독립적인 실행입니다.

```sh
uv run --locked python -m benefitfinder.jobs extract-chunked-facts \
  --input data/local/f12-context/input.json \
  --output-dir data/local/f12-context/new-run
```

입력은 기존 `rule-review`의 로컬 v1 `RuleReviewBatch`입니다. 공식 원문 1개, 연결된 `youth` 취업/주거 정책, 빈 `candidates`가 필요합니다. 원문은 전체 텍스트와 버전·공식 URL·수집 시각을 그대로 보존합니다. 승인 내보내기나 사용자 프로필은 모델에 보내지 않습니다. 실행 전에 Ollama와 E4B가 로컬에 설치되어 있어야 하며 자동 다운로드·원격 모델 호출은 하지 않습니다.

- `=== PDF PAGE N ===` 표식이 있으면 페이지를 원자 단위로 사용합니다. 표식이 없는 텍스트는 빈 줄로 나눈 문단을 사용합니다. 중심 묶음은 최대 두 단위이며, 앞뒤 한 단위를 통째로 함께 보여 줍니다. 첫/마지막 단위에 없는 이웃을 만들지 않습니다.
- 모든 원문 줄은 중심 범위에 정확히 한 번 포함됩니다. 문맥은 겹쳐 보여 주지만 원문을 승인 근거가 있는 구간만으로 축소하지 않습니다. 페이지/문단을 자르거나 예외만 별도로 제거하지 않습니다.
- 앞뒤 문맥까지 포함해 최대 220줄·6,000자입니다. 넘치면 중심 묶음을 한 단위로 줄이고, 그래도 안 맞으면 `chunk_context_limit`로 모델 호출 전에 실패합니다. 긴 페이지나 여러 쪽에 걸친 조항을 무조건 처리할 수 있다는 보장은 아닙니다.
- E4B에는 `think: false`, temperature 0, 16,384 토큰 문맥을 요청하고 로컬 JSON 스키마 응답을 사용합니다. 스키마는 네 분류에 실제 보여 준 비어 있지 않은 원문 줄 번호만 허용하며, 별도 줄 범위 검증은 그대로 유지합니다. 모델이 반환한 개별 줄 번호가 실제 보여 준 줄인지 검사하고 연속된 선택만 코드로 묶습니다. 인용문은 모델이 생성한 문장이 아니라 원본 줄에서 만듭니다. 범위/JSON 형식 오류는 같은 원문과 오류 안내로 E4B에 한 번만 수정을 요청하고 동일 검증을 다시 적용합니다. 범위를 코드로 교환·축소·버리지는 않습니다. 재요청 횟수와 사유를 묶음 기록에 남깁니다. 재검증 실패·잘린 응답·모델 오류는 전체 실패이며, 일부 성공을 전체 완료로 보고하지 않습니다.
- 명시적 `Q번호` 질문과 `A` 답변, 본문 뒤 `*`/`**` 각주는 원문 구조 단위로 연결합니다. 단위 전체를 보여 주는 묶음이 있어야 하며, 일부만 보이는 단위의 줄은 참고 문맥으로만 제시하고 그 묶음에서 선택하지 못하게 합니다. 단위 전체를 볼 수 없으면 모델 호출 전에 `chunk_context_limit`로 실패합니다. 모든 비어 있지 않은 원문 줄이 적어도 한 묶음에서 선택 가능한지도 검사합니다.
- 모델이 단위 일부를 선택하면 해당 FAQ의 질문·답변 또는 본문·각주 전체를 같은 분류의 인용에 붙입니다. 추가 문맥은 원본에서 가져오며 모델이 직접 선택한 것으로 표시하지 않습니다. 다음 FAQ, 독립된 기본 글머리표, 인식한 절 제목을 경계로 사용합니다. 표식 없는 문장·모호한 문서 구조와 먼 위치의 참조는 사람 확인이 필요합니다.
- 조건·제외·정의·계산식·주석을 포함한 범위와 별도 `exceptions` 범위를 요청합니다. 예외는 연결된 사실 분류와 문맥 기록에 함께 선택하도록 요청하되, 예외를 무조건적인 허용이나 승인으로 바꾸지 않습니다. 먼 페이지의 참조·FAQ 간 상충·예외의 의미적 완전성은 사람 검토 대상입니다.

새 출력 디렉터리만 사용합니다. 검증한 묶음은 `chunks/`에 즉시 보관하지만 완료를 뜻하지 않습니다. 모델 원선택은 겹친 줄과 공백만 합칩니다. 최종 후보는 인식한 FAQ/각주 단위의 원문 문맥을 추가해 보존합니다. `chunks/`의 `selected`(모델 원선택), `contextualized`(문맥 포함), `context_added`(추가 줄)를 구분합니다. `chunked-evidence.json`에는 `context_groups`, `model_only_facts`, `model_only_exceptions`를 함께 남겨 원선택과 문맥 보존 후 결과를 각각 평가할 수 있습니다. 성공하면 `facts-pending.json`(기존 로컬 v1), 전체 원문을 보존한 `rules-pending.json`(빈 규칙 검수 배치), `chunked-evidence.json`(중심/문맥 범위·모델 선택·별도 예외·원본 해시), 마지막에 `completion.json`을 생성합니다. `completion.json`이 없는 디렉터리는 미완료이며 평가 입력으로 사용하지 않습니다. 실패 시 남은 디렉터리는 재사용하지 말고 새 경로로 실행합니다. 기존 승인·보류·반려 파일을 바꾸거나 이전 결과를 덮어쓰지 않습니다.

금액은 모델이 직접 금액 근거로 선택한 줄의 명시된 단독 원화 수치만 읽습니다. 추가 문맥에 등장하는 소득·재산·다른 금액을 지원 금액으로 승격하지 않으며 합계·수급 자격·지급일을 추정하지 않습니다. `scheduled_monthly_day`는 `null`입니다. 추출은 항상 검수 대기이며 별도 예외 기록도 자동 승인·규칙 게시를 뜻하지 않습니다. `facts-pending.json`은 기존 로컬 v1의 필드 구조입니다. 긴 지침의 후보 수가 분류별 100개를 넘으면 기존 F12 파일 리더에 그대로 입력할 수 없습니다. 기존 한도를 완화하거나 상위 100개로 자르지 말고, 전체 후보의 형식·원문·버전·줄 위치를 검증한 뒤 공개 평가 함수에 연결해야 합니다. 현재 통합에는 기존 `evaluate-f12`도 포함하지만 분류별 100개 초과 후보를 평가하는 연결은 별도 이슈 #26으로 남아 있습니다.

콘솔에는 묶음 진행 건수와 고정 오류 코드만 표시합니다. 종료 코드 0은 추출 파일 생성, 1은 입력·모델·파일 오류, 2는 명령 인자 오류입니다. 원문·승인 기준·추출/평가 결과·실행 로그는 Git 제외 경로에만 보관하고 공유 테스트는 가상 문서를 사용합니다.

## 자동화 연결 경계

### F08 온통청년 갱신 한 번 실행

저장소 루트에서 `YOUTHCENTER_API_KEY`를 환경 변수로 설정한 뒤 실행한다. `--data-dir`로 다른 로컬 디렉터리를 지정할 수 있다.

```bash
uv run --locked python -m benefitfinder.jobs refresh-youthcenter
```

- `data/processed/f08-youthcenter/status.json`의 `last_success`는 마지막 성공본의 `run_id`, 완료 시각(KST), 이번 원천 목록 건수를 기록한다. 첫 실행부터 실패했다면 `null`이다. `last_attempt`에는 최근 실행의 성공 여부, 시각, 시도 횟수, 오류 코드, 성공한 변경 건수가 남는다.
- `last_success.run_id`로 `runs/<run_id>/youthcenter/`의 `records.json`, `records.csv`, `fields.csv`를 읽는다. 신규·변경·누락 이벤트가 있으면 해당 실행의 `changes.jsonl`도 생긴다. `runs/` 전체에서 최신 파일을 고르는 대신 이 포인터를 사용한다.
- 전체 수집과 JSON·CSV 생성이 끝나야 포인터를 바꾼다. 실패하면 이전 성공본의 파일과 완료 시각을 그대로 유지한다. 연결 오류, 일부 일시적인 5xx, 수집 중 전체 건수 변경은 5초 뒤 전체 수집을 한 번 더 시도한다. 응답 형식·권한·저장 실패는 실행 단위로 재시도하지 않는다. 상태 파일 교체가 실패하면 현재 상태를 다시 읽어, 이번 실행에서 만든 생성본이 선택되지 않았음이 확인된 경우에만 그 생성본을 정리한다. 교체가 실제로 끝났거나 상태를 재확인할 수 없으면 생성본을 보존하고 실패를 유지한다. 기존 성공본·과거 이력·다른 실행의 파일은 정리 대상이 아니다.
- 응답 원본은 `data/raw/f08-youthcenter/`에 보관한다. 이 명령은 수동 수집과 다른 경로를 쓰며, 결과는 원천 목록이다. 서비스에 노출할 취업·주거 분류, 검수·게시 데이터는 후속 단계에서 처리한다.
- 같은 데이터 디렉터리의 갱신은 `.refresh-lock`으로 중복 실행을 막는다. 강제 종료로 빈 잠금 디렉터리가 남았다면 실행 중인 프로세스가 없는 것을 확인한 뒤 제거한다. 종료 코드 `0`은 성공, `1`은 갱신 실패 또는 잠금 중, `2`는 키 누락 또는 잘못된 명령 인자다.

### 디스코드 성공·실패 알림

`data/local/f08.env`에 `YOUTHCENTER_API_KEY`와 `DISCORD_WEBHOOK_URL`을 넣고 파일 권한을 `600`으로 제한한다. 실제 값은 Git이나 명령 인자로 전달하지 않는다. `uv`가 파일을 읽어 실행 환경에 넣는다.

```bash
uv run --locked --env-file data/local/f08.env python -m benefitfinder.jobs \
  refresh-youthcenter --notify-discord
```

성공·실패, 실행 시각, 시도 횟수, 성공한 수집·변경 건수 또는 고정 오류 코드만 전송한다. 공고 본문·정책 ID·키·웹훅 주소는 메시지에 넣지 않는다. [Discord의 전송 확인 옵션](https://docs.discord.com/developers/resources/webhook#execute-webhook)을 사용하며 멘션은 비활성화한다. 갱신 실패는 종료 코드와 함께 유지한다. 알림 전송만 실패해도 종료 코드는 `1`이며, 완료된 수집을 다시 실행하거나 마지막 성공본을 되돌리지 않는다.

### macOS에서 매일 03:00 KST 실행

`launchd` 달력은 Mac의 시간대를 따르므로 시스템 시간대가 한국 표준시인 경우에만 설정 파일을 생성한다. 개발 브랜치 전환이 실행 코드를 바꾸지 않도록 커밋별 로컬 실행 사본을 만든다. 백그라운드 작업이 개발용 Documents 폴더에 의존하지 않도록 실행 코드·Python·환경 설정·운영 데이터는 `Library/Application Support/BenefitFinder/F08` 아래에 둔다. 비밀값과 운영 데이터는 코드 실행 사본과 분리한다.

```bash
repo_dir="$PWD"
service_dir="$HOME/Library/Application Support/BenefitFinder/F08"
runtime_dir="$service_dir/runtime/$(git rev-parse --short HEAD)"
mkdir -p "$runtime_dir/backend"
cp pyproject.toml uv.lock README.md "$runtime_dir/"
cp -R backend/src "$runtime_dir/backend/"
cp data/local/f08.env "$service_dir/f08.env"
chmod 600 "$service_dir/f08.env"
UV_PYTHON_INSTALL_DIR="$service_dir/python" uv python install 3.12 --no-bin
f08_python=$(UV_PYTHON_INSTALL_DIR="$service_dir/python" uv python find --managed-python 3.12)
uv sync --locked --no-dev --project "$runtime_dir" --python "$f08_python"
uv run --locked python scripts/f08_launchagent.py \
  --project-dir "$runtime_dir" --env-file "$service_dir/f08.env" \
  --data-dir "$service_dir/data" --output "$repo_dir/data/local/f08.plist"
plutil -lint data/local/f08.plist
mkdir -p "$HOME/Library/LaunchAgents"
cp data/local/f08.plist "$HOME/Library/LaunchAgents/dev.benefitfinder.f08.plist"
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/dev.benefitfinder.f08.plist"
```

생성기는 파일만 만들며 스케줄 등록은 마지막 명령이 수행한다. 설치 시 즉시 수집하지 않고 다음 03:00에 실행한다. 로그는 서비스 데이터 폴더의 `data/logs/f08.stdout.log`와 `f08.stderr.log`에 남는다. 키·웹훅을 변경하면 설치된 서비스의 `f08.env`도 갱신한다. 중지하려면 `launchctl bootout "gui/$(id -u)/dev.benefitfinder.f08"`을 실행한다. 갱신할 때에는 기존 작업을 먼저 중지하고 새 커밋의 실행 사본과 설정 파일을 만든다.

이 설정은 로그인한 Mac에서 동작한다. [Apple 문서](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/ScheduledJobs.html)에 따라 예정 시각에 잠자기 상태였다면 깨어날 때 실행되며, 전원이 꺼져 있으면 실행하지 못한다. 보조금24·기준금리·공고 원문 재분석·PostgreSQL 적재는 아직 연결하지 않았다.

자동화 주 담당은 탁서윤이다(2026-10-29). 실행·스케줄·재시도·실패 알림을 맡고, 윤정찬의 수집·원본 관리·PostgreSQL 적재와 Agent 분석·정제 흐름을 연결한다. 양승현은 API·계산 연동과 배포 환경의 통합 테스트를 맡는다. Agent 초안과 검수·게시 결과를 구분하고 미지원·상충·불명확은 검수 대기로 남긴다. 기존 모듈 의존 방향을 유지하며 jobs가 ai 내부를 직접 import하지 않고 조립 지점과 application 인터페이스로 연결한다.
