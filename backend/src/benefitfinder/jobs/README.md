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
- E4B에는 `think: false`, temperature 0, 16,384 토큰 문맥을 요청하고 로컬 JSON 스키마 응답을 사용합니다. 스키마는 네 분류에 실제 보여 준 범위 안의 개별 줄 번호만 허용하며, 별도 줄 범위 검증은 그대로 유지합니다. 모델이 반환한 개별 줄 번호가 실제 보여 준 줄인지 검사하고 연속된 선택만 코드로 묶습니다. 인용문은 모델이 생성한 문장이 아니라 원본 줄에서 만듭니다. 범위/JSON 형식 오류는 같은 원문과 오류 안내로 E4B에 한 번만 수정을 요청하고 동일 검증을 다시 적용합니다. 범위를 코드로 교환·축소·버리지는 않습니다. 재요청 횟수와 사유를 묶음 기록에 남깁니다. 재검증 실패·잘린 응답·모델 오류는 전체 실패이며, 일부 성공을 전체 완료로 보고하지 않습니다.
- 조건·제외·정의·계산식·주석을 포함한 범위와 별도 `exceptions` 범위를 요청합니다. 예외는 연결된 사실 분류와 문맥 기록에 함께 선택하도록 요청하되, 예외를 무조건적인 허용이나 승인으로 바꾸지 않습니다. 먼 페이지의 참조·FAQ 간 상충·예외의 의미적 완전성은 사람 검토 대상입니다.

새 출력 디렉터리만 사용합니다. 성공하면 `facts-pending.json`(기존 로컬 v1), 전체 원문을 보존한 `rules-pending.json`(빈 규칙 검수 배치), `chunked-evidence.json`(중심/문맥 범위·모델 선택·별도 예외·원본 해시), 마지막에 `completion.json`을 생성합니다. `completion.json`이 없는 디렉터리는 미완료이며 평가 입력으로 사용하지 않습니다. 실패 시 남은 디렉터리는 재사용하지 말고 새 경로로 실행합니다. 기존 승인·보류·반려 파일을 바꾸거나 이전 결과를 덮어쓰지 않습니다.

금액은 명시된 단독 원화 수치만 읽고 합계·수급 자격·지급일을 추정하지 않습니다. `scheduled_monthly_day`는 `null`입니다. 추출은 항상 검수 대기이며 별도 예외 기록도 자동 승인·규칙 게시를 뜻하지 않습니다. 기존 로컬 평가 도구가 있는 환경에서는 완료된 후보 두 파일을 입력으로 비교할 수 있습니다. 해당 평가 도구를 이 브랜치에 복사하거나 함께 병합하지 않습니다.

콘솔에는 묶음 진행 건수와 고정 오류 코드만 표시합니다. 종료 코드 0은 추출 파일 생성, 1은 입력·모델·파일 오류, 2는 명령 인자 오류입니다. 원문·승인 기준·추출/평가 결과·실행 로그는 Git 제외 경로에만 보관하고 공유 테스트는 가상 문서를 사용합니다.

## 자동화 연결 경계

수집·갱신 작업의 실행 진입점과 실패·재실행 제어를 둔다. 수집·정제 로직은 ingestion에 위임한다. 사용자 API 요청과 긴 작업을 분리하고 스케줄러·큐 제품은 필요할 때 정한다.

기획 명세 v2의 매일 03:00 KST 갱신, 기준금리 갱신, 실패 알림을 구현할 자리다. 실패한 실행으로 마지막 성공 시각을 갱신하지 않는다. 공고가 바뀌면 재검수 대상으로 되돌리고, 검수 완료 여부를 조합 계산에 전달한다. 구체 스케줄러·알림 연동은 아직 구현하지 않았다.

자동화 주 담당은 탁서윤이다(2026-10-29). 실행·스케줄·재시도·실패 알림을 맡고, 윤정찬의 수집·원본 관리·PostgreSQL 적재와 Agent 분석·정제 흐름을 연결한다. 양승현은 API·계산 연동과 배포 환경의 통합 테스트를 맡는다. Agent 초안과 검수·게시 결과를 구분하고 미지원·상충·불명확은 검수 대기로 남긴다. 기존 모듈 의존 방향을 유지하며 jobs가 ai 내부를 직접 import하지 않고 조립 지점과 application 인터페이스로 연결한다.
