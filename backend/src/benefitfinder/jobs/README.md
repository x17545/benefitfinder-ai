# 작업 실행

현재 구현: `python -m benefitfinder.jobs collect-youthcenter`가 온통청년 목록 수집을 한 번 실행한다(사용법은 [ingestion](../ingestion/README.md#로컬-수집-실행)). `rule-review`는 로컬 검수 자료의 준비·명시적 승인·재검수·상태 조회를 실행한다. 두 명령 모두 `__main__.py`에서 구체 파일 어댑터와 순수 처리 함수를 조립한다. 매일 실행·실패 알림은 구현하지 않았다.

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

두 파일의 공고 버전·해시와 인용을 저장된 원문으로 확인한 뒤 중복 제한·예외, 자격 조건, 지원 금액, 지급 시점을 검수 대기 상태와 함께 보여 준다. 공식 출처와 인용 위치를 표시하며, 추출된 근거가 없으면 `not_found`, 고정 월 지급일이 확인되지 않으면 `미확인`으로 표시한다. 파일이 빠졌거나 서로 다른 공고의 파일을 섞으면 내용을 출력하기 전에 실패한다. 조회는 읽기 전용이며 모델이나 공식 사이트를 호출하지 않는다. 중복 제한 상태 요약만 보려면 기존 `rule-review show --snapshot <경로>`를 사용한다.

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

모델은 자격 조건·지원 금액·지급 시점에 해당하는 원문 줄을 고른다. 결과는 원문 인용, 줄 번호, 문서 버전·해시가 붙은 별도 `needs_review` 파일이다. 금액 숫자는 인용에 직접 적힌 원화 표기만 옮긴다. 월 금액과 기간을 곱해 총액을 만들거나, 지급 시점의 상대 조건을 고정 지급일로 바꾸지 않는다. 근거를 찾지 못한 항목은 `not_found`, 명시된 고정 월 지급일이 없거나 하나로 확정할 수 없으면 `scheduled_monthly_day: null`이다. `not_found`는 후보에서 근거를 찾지 못했다는 뜻이며 원문 전체에 조건이 없다는 판정이 아니다. 이 파일은 아래 중복 제한 검수 명령의 승인·게시 입력이 아니다.

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

## 자동화 연결 경계

수집·갱신 작업의 실행 진입점과 실패·재실행 제어를 둔다. 수집·정제 로직은 ingestion에 위임한다. 사용자 API 요청과 긴 작업을 분리하고 스케줄러·큐 제품은 필요할 때 정한다.

기획 명세 v2의 매일 03:00 KST 갱신, 기준금리 갱신, 실패 알림을 구현할 자리다. 실패한 실행으로 마지막 성공 시각을 갱신하지 않는다. 공고가 바뀌면 재검수 대상으로 되돌리고, 검수 완료 여부를 조합 계산에 전달한다. 구체 스케줄러·알림 연동은 아직 구현하지 않았다.

자동화 주 담당은 탁서윤이다(2026-10-29). 실행·스케줄·재시도·실패 알림을 맡고, 윤정찬의 수집·원본 관리·PostgreSQL 적재와 Agent 분석·정제 흐름을 연결한다. 양승현은 API·계산 연동과 배포 환경의 통합 테스트를 맡는다. Agent 초안과 검수·게시 결과를 구분하고 미지원·상충·불명확은 검수 대기로 남긴다. 기존 모듈 의존 방향을 유지하며 jobs가 ai 내부를 직접 import하지 않고 조립 지점과 application 인터페이스로 연결한다.
