# 작업 실행

현재 구현: `python -m benefitfinder.jobs collect-youthcenter`가 온통청년 목록 수집을 한 번 실행한다(사용법은 [ingestion](../ingestion/README.md#로컬-수집-실행)). `rule-review`는 로컬 검수 자료의 준비·명시적 승인·재검수·상태 조회를 실행한다. 두 명령 모두 `__main__.py`에서 구체 파일 어댑터와 순수 처리 함수를 조립한다. 매일 실행·실패 알림은 구현하지 않았다.

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

모든 출력은 새 파일로만 만든다. 승인·재검수 후에도 이전 파일의 원문·검수자·시각은 그대로 남으며, 실패한 명령이 기존 자료를 바꾸지 않는다. 종료 코드 `0`은 명령 성공(검수 대기 결과도 포함), `1`은 형식·검수·파일 오류, `2`는 잘못된 명령 인자다. 오류는 고정 코드로 표시하며 콘솔에는 원문·인용문·외부 예외 본문을 출력하지 않는다.

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

## 자동화 연결 경계

수집·갱신 작업의 실행 진입점과 실패·재실행 제어를 둔다. 수집·정제 로직은 ingestion에 위임한다. 사용자 API 요청과 긴 작업을 분리하고 스케줄러·큐 제품은 필요할 때 정한다.

기획 명세 v2의 매일 03:00 KST 갱신, 기준금리 갱신, 실패 알림을 구현할 자리다. 실패한 실행으로 마지막 성공 시각을 갱신하지 않는다. 공고가 바뀌면 재검수 대상으로 되돌리고, 검수 완료 여부를 조합 계산에 전달한다. 구체 스케줄러·알림 연동은 아직 구현하지 않았다.

자동화 주 담당은 탁서윤이다(2026-10-29). 실행·스케줄·재시도·실패 알림을 맡고, 윤정찬의 수집·원본 관리·PostgreSQL 적재와 Agent 분석·정제 흐름을 연결한다. 양승현은 API·계산 연동과 배포 환경의 통합 테스트를 맡는다. Agent 초안과 검수·게시 결과를 구분하고 미지원·상충·불명확은 검수 대기로 남긴다. 기존 모듈 의존 방향을 유지하며 jobs가 ai 내부를 직접 import하지 않고 조립 지점과 application 인터페이스로 연결한다.
