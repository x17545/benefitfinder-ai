# F12 오프라인 분석 큐

공식 문서와 취업·주거 분류가 확인된 입력을 등록하면 짧은 PostgreSQL 임대 → LLM 도구 선택 → 로컬 검수 자료 → DB `needs_review` 순서로 실행한다. 이 경로는 계산용 정책이나 F13 조항을 승인·게시하지 않는다. 개발 환경에서 실제 API 표본의 검수 대기 자료를 생성했으며, 사람 승인·정책 사실 검증은 별도다. 공유 테스트는 가상 문서만 사용한다.

## 입력 준비

크롤링하지 않는다. 수집 DB의 목록으로 분야나 본문을 추정하지 않는다. 확인된 매핑이 있으면 [API 본문 입력 자동 준비](F12_PREPARATION.md)로 저장된 필드·원천 해시를 큐에 연결한다. 담당자가 별도로 확보한 공식 공고·지침·FAQ는 텍스트, 문서 버전·시각, 정책 이름·별칭, 현재 원천 연결을 확인해 아래 JSON으로 등록한다. `official: true`는 준비자의 확인값이며 URL만으로 공식성을 인증하는 기능은 아니다.

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

모델이 문서 조회, 조항 탐색, 정책 연결, 규칙 추출, 누락 검사, 후보 저장 중 다음 도구를 선택한다. 인용은 실제 조회된 줄·원문 해시·버전에 고정한다. 중복 제한은 앞뒤 두 줄의 조회된 문맥도 검수 사유로 보존하며, 명시적으로 언급된 카탈로그 정책을 건너뛰면 `overlap` 누락으로 남긴다. 언급만으로 규칙이나 정책 링크를 확정하지 않는다. 모호한 별칭은 연결하지 않으며 조건·예외·방향·시점·금액 영향을 검수 후보에 보존한다. 누락은 재탐색 후에도 찾지 못하면 누락으로 남긴다. 금액은 명시된 원화 표기만 코드로 읽으며 지급일·최종 자격·조합을 LLM이 계산하지 않는다.

분석기 `f12-tools-v2`는 이미 조회한 API 필드의 전체 문맥을 원문 그대로 보존한다. 수당 이름만으로 지급 시점이 확인됐다고 표시하지 않고, 신청 방법·기간과 사업 기간을 지급 시점에서 분리하며, 서비스 유형·기간은 서비스 근거로 남긴다. 소득 필드의 0을 지원금으로 읽지 않고 본인부담금·주택 거래금액·대출 원금·사업 예산은 지원금 후보 수치에서 제외한다. 단위가 공유된 금액 범위는 양 끝값을 보존하며 차감 순서·총 지급액은 계산하지 않는다. 주택 거래금액 산식, 자격의 AND/OR, 지급 조건·유형·기간·예외는 전체 필드 인용으로 확인한다. 모델이 건너뛴 명시적 신청·연령 필드도 조회가 끝난 뒤 보존한다. 연령 원시값은 `age` 부재와 구분하고 의미 확인은 `age_interpretation` 누락으로 남긴다. 중복 수혜 규칙을 찾지 못한 문서는 `overlap` 확인 대상으로 남긴다. 원문이 너무 커 기존 인용 한도를 넘으면 잘라 저장하지 않고 분석 실패로 표시한다.

알고리즘 보완 뒤 같은 입력의 비교 실행은 기존 완료 작업·원본 검수 자료를 덮어쓰지 않는 별도 로컬 평가 자료로 보관한다. 입력 해시·원천 최신 상태·기존 산출물 경로와 새 산출물 해시를 함께 기록해야 한다. 이 비교 자료는 DB 큐의 `output_ref`를 바꾸거나 사람 승인·운영 게시를 수행하지 않는다. 같은 입력을 재등록해도 예전 작업을 반환하므로 재등록을 재분석 완료로 보고하지 않는다.

F12는 도구 호출 응답을 필수로 요청하고 병렬 도구 생성을 끈다. 도구 종류는 계속 모델이 선택하며, 근거 조회·누락 검사·호출 수의 기존 한도는 유지한다. 도구 대신 일반 문장으로 종료하는 로컬 모델 응답과 한 번에 너무 많은 호출을 만드는 문제를 줄이기 위한 설정이다.

한 작업은 300초, 모델 호출 최대 48회, 한 응답의 도구 최대 4개, 전체 도구 최대 200개다. 개별 HTTP 호출은 남은 시간 내 최대 45초다. 10분 임대와 소유 토큰을 사용하고 최대 3회, 실패 뒤 최소 1분 후 다시 시도한다. 강제 종료는 임대 만료 후 새 토큰으로 복구한다. 모델 호출 중 DB 트랜잭션이나 원천 수집 잠금을 잡지 않는다.

`data/f12-analysis/<task_id>/<claim_token>/`에 입력, 도구 이름·고정 결과 코드·경과 시간만 담은 `trace.jsonl`, 문서별 기존 검수 쌍, 추가 신청·서비스·예외·사업 기간·원문 문맥 근거와 누락, 파일 해시의 `manifest.json`을 보존한다. 디렉터리는 700, 파일은 600이며 기존 실행 파일을 덮어쓰지 않는다. 부분 실패는 도구 기록과 고정 오류의 `failure.json`으로 남는다. 추가 근거만 있거나 아무 근거도 없는 문서는 빈 검수 쌍을 만들지 않고 누락/추가 근거 파일로 확인한다.

API 본문의 LF·CRLF를 원문과 해시에 그대로 보존한다. 줄 번호 인용은 선택 범위 끝의 줄바꿈만 생략할 수 있으며, 인용 내부의 CRLF를 LF로 바꾸거나 다른 줄의 문장을 가져오면 검수 파일 저장·읽기에서 거부한다.

전체 읽기가 끝나기 전 누락 검사를 한 뒤 나머지 본문을 읽었다면 다시 `check_missing`을 호출해야 저장할 수 있다. 저장 거부 응답은 이 재검사 동작을 모델에 명시하며 기존 읽기·누락 검사 요건을 유지한다.

파일 manifest는 파일 생성 완료 기록이다. DB 완료가 실패하거나 원천이 바뀐 경우에도 남을 수 있다. 검수 인계 전 `status --task-id`에서 `needs_review`, `source_current: true`, `output_ref`와 manifest의 SHA256이 일치하는지 확인한다. 원천 변경·누락은 실행 중이거나 완료한 작업도 `stale`로 바꾸며 파일은 이력으로 보존한다. 재등장·해시 복귀로 자동 복구하지 않는다. 새로 확인한 문서로 다시 등록해야 한다. `needs_review`도 사람 승인이 아니다. 기존 문서별 `rule-review show-all/decide/export-approved`와 카탈로그 검수·새 버전 게시 절차를 따른다. 추가 근거는 별도 사람 검수가 필요하다.

## F08 연결과 남은 범위

`refresh-data`에 `F12_WRITE_DATABASE_URL`이 설정되면 정책·ECOS 수집이 각각 끝난 뒤 준비된 대기 작업을 최대 5개 처리하고 단일 Discord 보고에 F12 단계 결과를 포함한다. 정책 수집이 실패해도 이미 준비된 독립 작업은 시도한다. 설정하지 않으면 기존 두 수집만 실행한다. 미분류·문서 미준비 원천은 등록하거나 모델에 보내지 않으며 `source_records_awaiting_scope_or_documents`로 구분한다. 이 값은 전체 활성 수집 레코드의 준비 대기 수이며 MVP 정책 수가 아니다.

확인된 정책 ID/분류와 저장된 API 본문은 [입력 자동 준비](F12_PREPARATION.md)로 연결한다. 실제 매핑 확인·사람 검수, API에 없는 첨부 공고·FAQ 확보/변경 추적, 보조금24, 운영 외부 모델 평가, 실제 API→03시 스케줄→Discord 도착은 후속이다. API 필드 변화 없이 첨부 문서만 바뀌는 경우의 자동 탐지와 게시 카탈로그 회수도 별도 수집 연계가 필요하다.

개발 GPU의 가상 문서 통합 검증은 규칙/20만 원/인접 예외 문맥 → 검수 대기와 금액 미기재 → 금액 추정 없음/누락 보존을 확인했다. 로컬 llama.cpp와 실제 PostgreSQL·CLI·검수 쌍 읽기·manifest 해시를 함께 검사했으며 게시 카탈로그 행은 0이었다. 실제 공고 품질·사람 검수·운영 외부 API 평가를 대신하지 않는다.
