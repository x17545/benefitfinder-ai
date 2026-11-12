# 수집 원천 변경과 재검수

0006은 수집 API의 내용 해시가 변경되거나 마지막 완전 생성본에서 정책이 누락될 때, 명시적으로 연결된 게시 정책의 승인을 해제한다. 이름·URL·ID 접두어로 연결 대상을 추정하지 않는다. 원천의 분야·정책 사실과 공식 지침 내용은 사람이 확인하며 실제 자료를 자동 승인하거나 F12를 호출하지 않는다.

## 명시적인 원천 연결

[게시 카탈로그 입력](POLICY_CATALOG.md)의 `collection_sources`는 최대 20개이며 `(source, external_id)` 중복은 거부한다. 현재 원천은 `youthcenter`다. 실제 정책은 비어 있으면 검증·게시할 수 없다. 기존 가상 정책만 빈 배열을 허용하며 빈 배열의 승인 해시는 이전 형식과 같다.

```json
{
  "collection_sources": [
    {
      "source": "youthcenter",
      "external_id": "fictional-source-policy-id",
      "material_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    }
  ]
}
```

위 값은 가상이다. 실제 값은 보존한 수집 레코드의 ID·내용 해시를 검수해 기록한다. 공식 원문 텍스트의 `source_sha256`과 API 레코드의 `material_sha256`은 서로 다른 해시다. 다른 정책의 제한에 의존하는 규칙은 그 상대 원천도 명시적으로 연결해야 변경을 감지한다. 배열 전체가 승인 해시에 포함되며 수정하면 새 검수가 필요하다. F12 승인 내보내기를 자동 변환하지 않는다.

게시 시 각 원천이 DB에 존재하고 누락되지 않았으며 현재 내용 해시가 같은지 확인한다. 승인이 해당 내용의 마지막 신규·변경·재등장 감지보다 앞서면 거부한다. 내용이 같은 일상 재수집은 기존 승인을 유지한다. 수집과 게시가 같은 원천 잠금을 사용하므로 변경 중에 이전 해시를 새 버전에 게시할 수 없다. 잠금·검증 실패는 이전 게시 버전 전환까지 롤백한다.

## 적용과 데이터 영향

0001·0002·0005 다음에 신뢰하는 마이그레이션 관리 역할로 명시적으로 실행한다. 운영·공유 DB 적용은 별도 승인 범위다. 서버 시작이나 수집 명령은 자동 DDL을 실행하지 않는다.

```sh
psql "$DATABASE_URL" -X --set=ON_ERROR_STOP=1 --single-transaction \
  --file=backend/migrations/versions/0006_policy_source_rechecks.sql
```

**기존 실제 게시 정책은 검증된 원천 연결이 없으므로 `pending`·`retired`로 전환하며 `source_unlinked` 기록을 만든다.** 같은 정책의 기존 F13 문서·조항도 게시에서 내린다. 원문·승인 해시·검수자·검수일·과거 게시일은 보존한다. 기존 가상 게시 정책은 유지한다. 실제 자료가 있는 DB에는 영향 대상을 확인하고 새 원천 연결·사람 검수·새 문서 버전의 재게시를 준비한 뒤 적용한다.

수집 적재는 [기존 전용 쓰기 역할](COLLECTED_POLICIES.md#저장-경계와-권한)을 쓴다. 이 역할에 게시 정책·F13·재검수 큐의 SELECT/UPDATE를 추가하지 않는다. 소유자의 제한된 `SECURITY DEFINER` 트리거가 고정된 스키마에서 무효화만 수행한다. 함수의 PUBLIC 실행 권한은 제거하며 수집·앱 역할을 테이블/함수 소유자로 사용하지 않는다. 원천 테이블 직접 편집 대신 완전한 수집 인계 명령을 사용한다.

## 원자적인 무효화와 보존

- `policy_catalog_source_links`는 승인된 게시 입력에서만 원천 연결을 저장한다. 같은 정책/문서 버전의 payload와 승인 해시는 이후 수정하지 못한다.
- 새 변경 이벤트와 연결된 현재 게시 정책은 `pending`·`retired`가 된다. 같은 정책에 저장된 모든 F13 문서 버전과 조항도 게시에서 내리고 승인된 조항을 검수 대기로 되돌린다. 독립된 다른 정책의 게시 상태는 유지한다.
- `policy_recheck_queue`에는 원천 이벤트·승인 시/변경 시 해시·기존 검수 정보·무효화한 F13 버전을 남긴다. 수집 저장·변경 이벤트·승인 해제·큐 기록은 한 트랜잭션이다. 어느 단계든 실패하면 직전 수집 성공본과 게시 상태를 함께 보존한다.
- 같은 생성본 재적재는 기록을 중복 생성하지 않는다. 재등장·이전 내용 복귀도 자동 재승인이 아니다. 무효화한 정책/문서 버전은 직접 상태 변경으로 다시 게시하지 못한다. 최신 원천과 공식 원문을 검수한 **새 버전**이 필요하다.
- 이미 내린 정책에는 후속 변경마다 같은 작업을 중복 생성하지 않는다. 목록은 당시 이벤트와 현재 원천 해시·누락 상태를 함께 표시하므로 최신 자료를 다시 확인한다. 이후 새 검수 버전이 게시되면 기존 기록은 `superseded`와 새 버전을 표시하며 감사 이력으로 보존한다.

## 재검수 목록 내보내기

전용 읽기 역할에 필요한 열만 부여할 수 있다. `reviewed_by`가 포함된 결과는 검수자 작업 자료이며 공개 응답·로그에 넣지 않는다.

```sql
GRANT CONNECT ON DATABASE benefitfinder TO benefitfinder_recheck_reader;
GRANT USAGE ON SCHEMA public TO benefitfinder_recheck_reader;
GRANT SELECT ON public.policy_recheck_queue TO benefitfinder_recheck_reader;
GRANT SELECT (source, external_id, material_sha256, missing_detected_at, last_run_id)
    ON public.collected_policies TO benefitfinder_recheck_reader;
GRANT SELECT (policy_id, document_version, published_at)
    ON public.policy_catalog_releases TO benefitfinder_recheck_reader;
```

접속 문자열은 `POLICY_RECHECK_DATABASE_URL`로 전달한다. `.env`를 자동 로드하지 않는다. 부모 디렉터리가 있는 새 경로에 권한 600으로 원자적으로 생성하며 기존 파일·심볼릭 링크를 덮어쓰지 않는다. 실제 결과는 Git 제외 경로에 둔다.

```sh
uv run --locked --env-file data/local/review.env python -m benefitfinder.jobs \
  policy-catalog rechecks --output data/local/rechecks/page-1.json --limit 100
```

`format_version: 1`, `kind: policy_recheck_page`, `items`, `next_after_id`를 반환한다. `task_id` 오름차순으로 최대 1,000개까지 읽고 다음 페이지가 있으면 `--after-id <next_after_id>`로 이어 간다. 원천 전문·프로필은 포함하지 않으며 콘솔에는 건수와 다음 커서만 출력한다. 성공은 0, 설정·입력·DB·파일 실패는 고정 오류와 2다. 목록 조회는 승인·처리 완료·게시에 영향을 주지 않는다.

이 단계는 F08의 API 레코드 변경/누락에 대한 연결과 재검수 인계다. API 내용이 그대로인 외부 공식 지침 파일의 변경 감지, 신규 미분류 원천의 분야 확인, F12 자동 분석·큐 소비·검수 완료 기록과 운영 게시 워크플로우는 후속이다. 자동 분석도 최신 공식 원문·출처·정책 범위 확인을 거쳐야 하며 사람이 검수하지 않은 후보를 계산에 넣지 않는다.
