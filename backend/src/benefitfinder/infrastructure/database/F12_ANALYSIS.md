# F12 오프라인 분석 큐

공식 문서와 취업·주거 분류가 확인된 입력을 등록하면 짧은 PostgreSQL 임대 → LLM 도구 선택 → 로컬 검수 자료 → DB `needs_review` 순서로 실행한다. 이 경로는 계산용 정책이나 F13 조항을 승인·게시하지 않는다. 실제 정책 검수 자료는 아직 준비되지 않았으며 공유 테스트는 가상 문서만 사용한다.

## 입력 준비

크롤링하지 않는다. 원문 확보 방법은 현행 명세의 미정 항목이다. 수집 DB에 목록이 있다는 이유만으로 분야나 공식 문서의 본문을 추정하지 않는다. 담당자가 공식 API·공고·지침·FAQ에서 확보한 텍스트, 문서 버전·시각, 정책 이름·별칭, 현재 수집 레코드와의 연결을 확인해 로컬 JSON을 준비한다. 문서의 `official: true`는 준비자의 확인값이며 서버가 URL만 보고 공식성을 인증하는 기능은 아니다.

입력은 `format_version: 1`과 다음 필드만 받는다. 내부 작업 형식이며 공개 API 계약은 바꾸지 않는다.

| 필드 | 내용 |
| --- | --- |
| `policy_id` | 분석할 `youth:` 정책 ID |
| `documents` | 1~4개 공식 문서. `id`, `policy_id`, HTTPS `source_url`, `document_version`, 시간대 있는 `collected_at`, 정확한 `text`, `official: true` |
| `policies` | 확인된 청년 취업·주거 정책 1~100개. `id`, `name`, `categories` (`employment`/`housing`), `aliases` 배열. 분석 대상도 포함 |
| `collection_sources` | 1~20개 현재 원천. `source: "youthcenter"`, `external_id`, 수집 DB의 `material_sha256` |

문서는 각 10만 자·합계 20만 자, 입력은 2MiB 이하로 제한한다. 분야·별칭·문서 ID의 상한과 중복 키를 검사하며 승인값·사용자 프로필·알 수 없는 필드를 거부한다. 연결한 모든 원천이 현재 활성 상태이며 해시가 일치해야 한다. 문서 확인 시각은 마지막 내용 변경·재등장 이후, DB 현재 시각 이하여야 한다. 실제 키·원문·결과는 Git 제외 `data/`에 보관한다.

## DB와 실행

관리자는 0001·0002·0005·0006 이후 `backend/migrations/versions/0007_f12_analysis_tasks.sql`을 명시적으로 적용한다. 자동 DDL·자료 삽입은 없다. 공유·운영 DB 적용은 별도 승인 작업이다.

전용 작업 역할을 관리자가 만든 뒤 다음 권한만 준다. 수집 역할은 기존 권한을 유지하며 원천 변경 트리거가 작업을 무효화한다. 작업 역할에 계산용 카탈로그 쓰기·스키마 생성·테이블 삭제 권한을 주지 않는다.

```sql
GRANT USAGE ON SCHEMA public TO benefitfinder_f12;
GRANT SELECT ON public.collected_policies, public.collected_policy_runs,
    public.collected_policy_changes, public.policy_recheck_queue TO benefitfinder_f12;
GRANT SELECT, INSERT, UPDATE ON public.f12_analysis_tasks TO benefitfinder_f12;
GRANT SELECT, INSERT ON public.f12_analysis_sources TO benefitfinder_f12;
GRANT USAGE, SELECT ON SEQUENCE public.f12_analysis_tasks_task_id_seq TO benefitfinder_f12;
```

연결 문자열은 `F12_WRITE_DATABASE_URL`에 설정한다. 개발은 `LLM_PROVIDER=llama_cpp`, 운영은 `LLM_PROVIDER=external_api`와 해당 환경의 모델·주소·키를 명시한다. [LLM 설정](../../../../../docs/development/LOCAL_LLM.md)을 따른다. 모델이 비활성 상태면 작업을 확보하기 전에 실패한다.

```sh
uv run --locked python -m benefitfinder.jobs f12-analysis register --input data/local/f12-input.json
uv run --locked python -m benefitfinder.jobs f12-analysis run --data-dir data --limit 5
uv run --locked python -m benefitfinder.jobs f12-analysis status
uv run --locked python -m benefitfinder.jobs f12-analysis status --task-id 1
```

기존 재검수 이벤트에 연결하려면 등록 시 `--recheck-id <ID>`를 추가한다. 다른 정책·원천의 이벤트 연결은 거부한다. 재검수 이벤트를 완료·승인 처리하지 않는다. 같은 입력 해시의 재등록은 기존 작업을 반환하고 LLM을 다시 호출하지 않는다. 같은 정책의 새 공식 입력은 이전 작업·초안을 `stale`로 만든다. 내용이 같은 API 재수집은 완료한 분석을 다시 실행하지 않는다.

## 실행·실패·검수

모델이 문서 조회, 조항 탐색, 정책 연결, 규칙 추출, 누락 검사, 후보 저장 중 다음 도구를 선택한다. 인용은 실제 조회된 줄·원문 해시·버전에 고정한다. 모호한 별칭은 연결하지 않으며 조건·예외·방향·시점·금액 영향을 검수 후보에 보존한다. 누락은 재탐색 후에도 찾지 못하면 누락으로 남긴다. 금액은 명시된 원화 표기만 코드로 읽으며 지급일·최종 자격·조합을 LLM이 계산하지 않는다.

한 작업은 300초, 모델 호출 최대 48회, 한 응답의 도구 최대 4개, 전체 도구 최대 200개다. 개별 HTTP 호출은 남은 시간 내 최대 45초다. 10분 임대와 소유 토큰을 사용하고 최대 3회, 실패 뒤 최소 1분 후 다시 시도한다. 강제 종료는 임대 만료 후 새 토큰으로 복구한다. 모델 호출 중 DB 트랜잭션이나 원천 수집 잠금을 잡지 않는다.

`data/f12-analysis/<task_id>/<claim_token>/`에 입력, 도구 이름·고정 결과 코드·경과 시간만 담은 `trace.jsonl`, 문서별 기존 검수 쌍, 추가 신청·서비스·예외 근거와 누락, 파일 해시의 `manifest.json`을 보존한다. 디렉터리는 700, 파일은 600이며 기존 실행 파일을 덮어쓰지 않는다. 부분 실패는 도구 기록과 고정 오류의 `failure.json`으로 남는다. 추가 근거만 있거나 아무 근거도 없는 문서는 빈 검수 쌍을 만들지 않고 누락/추가 근거 파일로 확인한다.

파일 manifest는 파일 생성 완료 기록이다. DB 완료가 실패하거나 원천이 바뀐 경우에도 남을 수 있다. 검수 인계 전 `status --task-id`에서 `needs_review`, `source_current: true`, `output_ref`와 manifest의 SHA256이 일치하는지 확인한다. 원천 변경·누락은 실행 중이거나 완료한 작업도 `stale`로 바꾸며 파일은 이력으로 보존한다. 재등장·해시 복귀로 자동 복구하지 않는다. 새로 확인한 문서로 다시 등록해야 한다. `needs_review`도 사람 승인이 아니다. 기존 문서별 `rule-review show-all/decide/export-approved`와 카탈로그 검수·새 버전 게시 절차를 따른다. 추가 근거는 별도 사람 검수가 필요하다.

## F08 연결과 남은 범위

`refresh-data`에 `F12_WRITE_DATABASE_URL`이 설정되면 정책·ECOS 수집이 각각 끝난 뒤 준비된 대기 작업을 최대 5개 처리하고 단일 Discord 보고에 F12 단계 결과를 포함한다. 정책 수집이 실패해도 이미 준비된 독립 작업은 시도한다. 설정하지 않으면 기존 두 수집만 실행한다. 미분류·문서 미준비 원천은 등록하거나 모델에 보내지 않으며 `source_records_awaiting_scope_or_documents`로 구분한다. 이 값은 전체 활성 수집 레코드의 준비 대기 수이며 MVP 정책 수가 아니다.

공식 본문 확보·정책 ID/분류 확인·신규 입력 자동 준비, 실제 사람 검수, 보조금24, 운영 외부 모델 평가, 실제 API→03시 스케줄→Discord 도착은 후속이다. 이 큐의 성공을 이 단계들의 완료로 보지 않는다. API 필드 변화 없이 공식 첨부 문서만 바뀌는 경우의 자동 탐지와 게시 카탈로그 회수도 별도 수집 연계가 필요하다.
