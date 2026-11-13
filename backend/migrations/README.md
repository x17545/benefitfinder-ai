# PostgreSQL 마이그레이션

`versions/0001_f13_published_clauses.sql`은 F13 문서·조항·게시 상태와 `public.benefitfinder_schema_migrations` 이력을 만든다. psycopg 드라이버를 도입했으며 별도 ORM·마이그레이션 프레임워크는 사용하지 않는다. 윤정찬·양승현이 저장 모델·데이터 영향을 함께 검토한다. 앱 시작 시 자동 DDL·자동 게시를 수행하지 않는다.

관리 역할의 `DATABASE_URL`을 셸에 설정한 뒤 저장소 루트에서 명시적으로 적용한다. URL을 명령행에 직접 쓰거나 공유 파일에 넣지 않는다. PostgreSQL의 `psql` 도구가 필요하다.

```sh
psql "$DATABASE_URL" -X --set=ON_ERROR_STOP=1 --single-transaction \
  --file=backend/migrations/versions/0001_f13_published_clauses.sql
psql "$DATABASE_URL" -X --command='TABLE public.benefitfinder_schema_migrations;'
```

최초 적용은 단일 트랜잭션이다. 오류가 나면 테이블 생성과 이력까지 롤백한다. 적용 후 문서·조항은 비어 있으며 실제 자료나 가상 자료를 자동으로 넣지 않는다. 같은 파일을 다시 적용하면 기존 테이블 오류로 실패하며 덮어쓰지 않는다. 먼저 이력의 `0001_f13_published_clauses`를 확인한다. 이후 변경은 새 번호의 SQL 파일로 추가하고 이미 공유한 이력을 수정·삭제하지 않는다.

게시 버전 전환은 한 트랜잭션에서 이전 문서 `retired` → 새 문서 `published` 순서로 진행한다. 조항은 검수 승인과 게시를 별도 단계로 기록한다. F12 승인 파일은 게시 입력을 검토하기 위한 자료이며 이 마이그레이션이나 서버가 자동 게시하지 않는다.

실패 복구는 트랜잭션 롤백을 기본으로 한다. 공유 데이터가 생긴 후에는 되돌리기용 새 마이그레이션을 검토하며 테이블 DROP 명령을 자동 제공하지 않는다. 운영·공유 DB 적용과 파괴적 변경은 승인된 별도 작업이다.

[전용 테스트 DB 검증](../integration/README.md)은 실제 PostgreSQL에서 생성·읽기·쓰기·버전 전환·검수/게시 제외·장애를 확인한다. 가상 사례만 사용하고 원문·결과·덤프·자격 증명은 Git에 넣지 않는다.

`testing/0001_f12_review_imports.sql`은 [F12 미게시 검증](../integration/README.md#실제-f12-승인-근거의-미게시-검증)에만
쓰는 출처 이력이다. 로컬의 별도 `_test` DB에서 versions/0001 다음에 명시적으로 적용한다.
원본 ID·후보·승인 사유·원문/쌍/내보내기 해시를 보존하며 반려·대기 항목은 적재하지 않는다.
게시 상태를 바꾸지 않으며 운영용 마이그레이션 실행 목록에 포함하지 않는다.

## 게시 정책 카탈로그 (0002)

`versions/0002_published_policy_catalog.sql`은 정책·조건 실행 규칙·지급 일정·승인 해시·검수/게시 상태를 저장한다. 0001 다음에 별도 트랜잭션으로 적용한다. 기존 F13 조항을 바꾸거나 정책을 자동 게시하지 않는다.

```sh
psql "$DATABASE_URL" -X --set=ON_ERROR_STOP=1 --single-transaction \
  --file=backend/migrations/versions/0002_published_policy_catalog.sql
```

적용 후 카탈로그는 비어 있다. [입력·검수·실행 절차](../src/benefitfinder/infrastructure/database/POLICY_CATALOG.md)를 따른다. 공유·운영 DB에는 별도 승인 없이 적용하지 않는다.
