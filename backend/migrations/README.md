# PostgreSQL 마이그레이션

`versions/0011_f12_analysis_versions.sql`은 0007 이후 별도 트랜잭션으로 명시적으로 적용한다. 기존 분석 행을 버전 1로 표시하고, 원문 해시와 별개인 분석 버전을 등록·임대·완료·manifest에 연결한다. 기존 원문·초안 파일·사람 판단·게시 상태는 바꾸지 않는다. 적용 전 F12 작업자를 모두 중지하고 같은 새 코드로 교체한 뒤 재개한다. 구 코드와 혼합 실행하지 않는다. 새 버전에서 같은 공식 입력을 준비하면 별도 작업을 만들고 이전 초안을 `f12_analysis_version_changed` 사유로 `stale` 처리한다. 재시도 횟수 초기화·이전 파일 덮어쓰기·자동 승인·자동 게시가 아니다. [분석 큐의 버전 전환 절차](../src/benefitfinder/infrastructure/database/F12_ANALYSIS.md)를 따른다. 운영·공유 DB 적용은 별도 승인 범위다.

`versions/0010_catalog_explanation_publication.sql`은 0009 이후 명시적으로 적용한다. 승인 카탈로그의 `explanation_clauses` 전체를 같은 트랜잭션에서 F13 문서·조항으로 만들고 승인 해시·검수 정보에 연결한다. 기존 게시 역할에 F13 쓰기 권한을 추가하지 않는다. 카탈로그 은퇴·원천 변경 시 함께 내리며 연결 없는 기존 F13은 유지한다. [입력·검수·권한·같은 버전 제약](../src/benefitfinder/infrastructure/database/F12_CATALOG.md)을 확인한다. 운영 적용은 별도 승인 범위다.

`versions/0006_policy_source_rechecks.sql`은 0001·0002·0005 다음에 명시적으로 적용한다. 승인 해시에 포함한 원천 ID·내용 해시를 연결하고 수집 변경·누락 시 게시 정책·F13을 내리며 재검수 이력을 같은 트랜잭션으로 기록한다. **기존 실제 게시 정책은 검증된 연결이 없어 검수 대기/은퇴로 전환하며 기존 가상 정책은 유지한다.** 운영 적용 전 [데이터 영향·재게시·권한](../src/benefitfinder/infrastructure/database/POLICY_SOURCE_RECHECKS.md)을 확인한다. 새 실제 정책 게시에는 이 마이그레이션과 원천 연결이 필요하다.

`versions/0005_collected_policy_generations.sql`은 완전한 수집 성공본·최신 원천 레코드·변경 이력을 저장한다. 0001의 이력 테이블 다음에 별도 트랜잭션으로 적용한다. 미검수 원천 영역이며 기존 게시 정책·F13 조항·승인 상태를 바꾸지 않는다. [적재 설정·재실행·권한 경계](../src/benefitfinder/infrastructure/database/COLLECTED_POLICIES.md)를 따른다.

`versions/0004_ecos_base_rate.sql`은 마지막 성공한 ECOS 기준금리·관측일·확인 시각과 고정 출처 코드를 저장한다. 0001의 이력 테이블 이후 별도 트랜잭션으로 적용한다. 0002·0003의 데이터는 바꾸지 않는다. 초기 금리·사용자 정보·API 키를 넣지 않으며 [수집·권한·운영 범위](../src/benefitfinder/infrastructure/database/ECOS_RATES.md)를 따른다.

`versions/0001_f13_published_clauses.sql`은 F13 문서·조항·게시 상태와 `public.benefitfinder_schema_migrations` 이력을 만든다. psycopg 드라이버를 도입했으며 별도 ORM·마이그레이션 프레임워크는 사용하지 않는다. 윤정찬·양승현이 저장 모델·데이터 영향을 함께 검토한다. 앱 시작 시 자동 DDL·자동 게시를 수행하지 않는다.

관리 역할의 `DATABASE_URL`을 셸에 설정한 뒤 저장소 루트에서 명시적으로 적용한다. URL을 명령행에 직접 쓰거나 공유 파일에 넣지 않는다. PostgreSQL의 `psql` 도구가 필요하다.

```sh
psql "$DATABASE_URL" -X --set=ON_ERROR_STOP=1 --single-transaction \
  --file=backend/migrations/versions/0001_f13_published_clauses.sql
psql "$DATABASE_URL" -X --command='TABLE public.benefitfinder_schema_migrations;'
```

최초 적용은 단일 트랜잭션이다. 오류가 나면 테이블 생성과 이력까지 롤백한다. 적용 후 문서·조항은 비어 있으며 실제 자료나 가상 자료를 자동으로 넣지 않는다. 같은 파일을 다시 적용하면 기존 테이블 오류로 실패하며 덮어쓰지 않는다. 먼저 이력의 `0001_f13_published_clauses`를 확인한다. 이후 변경은 새 번호의 SQL 파일로 추가하고 이미 공유한 이력을 수정·삭제하지 않는다.

게시 버전 전환은 한 트랜잭션에서 이전 문서 `retired` → 새 문서 `published` 순서로 진행한다. 조항은 검수 승인과 게시를 별도 단계로 기록한다. F12 승인 파일은 게시 입력을 검토하기 위한 자료이며 이 마이그레이션이나 서버가 자동 게시하지 않는다.

`versions/0003_analysis_usage.sql`은 추천·상황 변화를 합친 KST 날짜별 100회 집계다. 0001의 이력 테이블 이후 명시적으로 적용하며 정책 카탈로그의 0002와는 독립적이다. 날짜와 횟수만 저장하고 사용자 조건은 저장하지 않는다. [권한·실행 설정](../src/benefitfinder/infrastructure/database/ANALYSIS_USAGE.md)을 따른다.

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

## F12 오프라인 분석 (0007)

`versions/0007_f12_analysis_tasks.sql`은 0001·0002·0005·0006 다음에 명시적으로 적용한다. 입력 해시·원천 연결·임대·시도 횟수·검수 대기 파일 해시를 보존하고 원천 변경/누락 시 기존 분석을 무효화한다. 기존 게시 카탈로그를 자동 승인·게시하지 않는다. [최소 권한·실행·실패 복구](../src/benefitfinder/infrastructure/database/F12_ANALYSIS.md)를 따른다.

## F14 게시 서비스 (0008)

`versions/0008_service_catalog.sql`은 0001·0002·0005·0006 다음에 명시적으로 적용한다. 0007과 독립적이다. 같은 카탈로그에서 서비스와 현금 입력/정책 ID 종류를 구분하고 기존 승인·원천 변경 무효화 흐름을 재사용한다. 기존 현금 자료를 삭제하거나 자동 게시하지 않는다. [단가 근거·권한·검수 절차](../src/benefitfinder/infrastructure/database/SERVICE_CATALOG.md)를 따른다.

## 보조금24 소스 확장 (0009)

`versions/0009_gov24_policy_sources.sql`은 0005·0006·0007·0008 다음에 별도 마이그레이션 트랜잭션으로 적용한다. 기존 수집·카탈로그 연결·F12 테이블에 `gov24`를 허용하며 다른 소스는 계속 거부한다. 승인·원천·게시 이력을 보존하고 수집/게시/F13 쓰기의 잠금 순서를 `gov24` → `youthcenter` → 정책/행으로 통일한다. 직접 SQL 카탈로그 변경도 행 잠금 전에 같은 순서를 따른다. 운영 자동 적용은 하지 않는다. [실행·자료 영향](../src/benefitfinder/infrastructure/database/GOV24.md)을 검토한다.

## 공식 첨부 문서 원천 (0012)

`versions/0012_official_document_sources.sql`은 0011 이후 명시적으로 적용한다. 기존 수집·승인 연결·F12 원천에 `official_documents`를 추가한다. 승인·게시·원본 이력은 보존하며 자동 게시·DDL은 없다. 쓰기 잠금 순서를 `gov24` → `official_documents` → `youthcenter` → 정책/행으로 맞춘다. [공식 HTML·PDF 수집과 검수 연결](../src/benefitfinder/infrastructure/database/OFFICIAL_DOCUMENTS.md)을 확인하고 저장 모델 담당자의 리뷰를 받는다.
