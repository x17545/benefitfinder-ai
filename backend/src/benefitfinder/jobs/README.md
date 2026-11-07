# 작업 실행

현재 구현: `collect-youthcenter`는 [수동 수집](../ingestion/README.md#로컬-수집-실행)을, `refresh-youthcenter`는 [이전 성공본을 보존하는 F08 갱신](#f08-온통청년-갱신-한-번-실행)을 실행한다. `rule-review`는 로컬 검수 자료의 준비·명시적 승인·재검수·상태 조회를 실행한다. 매일 실행과 외부 알림은 아직 연결하지 않았다.

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

## 자동화 연결 경계

### F08 온통청년 갱신 한 번 실행

저장소 루트에서 `YOUTHCENTER_API_KEY`를 환경 변수로 설정한 뒤 실행한다. `--data-dir`로 다른 로컬 디렉터리를 지정할 수 있다.

```bash
uv run --locked python -m benefitfinder.jobs refresh-youthcenter
```

- `data/processed/f08-youthcenter/status.json`의 `last_success`는 마지막 성공본의 `run_id`, 완료 시각(KST), 이번 원천 목록 건수를 기록한다. 첫 실행부터 실패했다면 `null`이다. `last_attempt`에는 최근 실행의 성공 여부, 시각, 시도 횟수, 오류 코드, 성공한 변경 건수가 남는다.
- `last_success.run_id`로 `runs/<run_id>/youthcenter/`의 `records.json`, `records.csv`, `fields.csv`를 읽는다. 신규·변경·누락 이벤트가 있으면 해당 실행의 `changes.jsonl`도 생긴다. `runs/` 전체에서 최신 파일을 고르는 대신 이 포인터를 사용한다.
- 전체 수집과 JSON·CSV 생성이 끝나야 포인터를 바꾼다. 실패하면 이전 성공본의 파일과 완료 시각을 그대로 유지한다. 연결 오류, 일부 일시적인 5xx, 수집 중 전체 건수 변경은 5초 뒤 전체 수집을 한 번 더 시도한다. 응답 형식·권한·저장 실패는 실행 단위로 재시도하지 않는다.
- 응답 원본은 `data/raw/f08-youthcenter/`에 보관한다. 이 명령은 수동 수집과 다른 경로를 쓰며, 결과는 원천 목록이다. 서비스에 노출할 취업·주거 분류, 검수·게시 데이터는 후속 단계에서 처리한다.
- 같은 데이터 디렉터리의 갱신은 `.refresh-lock`으로 중복 실행을 막는다. 강제 종료로 빈 잠금 디렉터리가 남았다면 실행 중인 프로세스가 없는 것을 확인한 뒤 제거한다. 종료 코드 `0`은 성공, `1`은 갱신 실패 또는 잠금 중, `2`는 키 누락 또는 잘못된 명령 인자다.

현재 범위는 온통청년 원천 목록의 로컬 갱신 실행이다. 매일 03:00 KST 스케줄, 보조금24, 기준금리, 공고 원문 재분석, PostgreSQL 적재, 디스코드 보고는 아직 연결하지 않았다.

자동화 주 담당은 탁서윤이다(2026-10-29). 실행·스케줄·재시도·실패 알림을 맡고, 윤정찬의 수집·원본 관리·PostgreSQL 적재와 Agent 분석·정제 흐름을 연결한다. 양승현은 API·계산 연동과 배포 환경의 통합 테스트를 맡는다. Agent 초안과 검수·게시 결과를 구분하고 미지원·상충·불명확은 검수 대기로 남긴다. 기존 모듈 의존 방향을 유지하며 jobs가 ai 내부를 직접 import하지 않고 조립 지점과 application 인터페이스로 연결한다.
