# 실제 PostgreSQL 검증

`test_policy_preparation_postgres.py`는 저장 API 본문→F12 입력의 정확한 텍스트/해시, 같은 생성본의 재사용, 변경·누락·재등장, 분류 불일치의 독립 처리, 원천 갱신 경쟁·동시 등록·최소 작업 권한, CLI와 일일 준비→분석 순서를 가상 자료로 확인한다. 실제 정책 자료·사람 검수·운영 정시 실행은 포함하지 않는다.

`test_policy_source_rechecks_postgres.py`는 별도 UUID `_test` DB에 0001·0002·0005·0006을 적용해 명시적 원천 연결·승인 시각·변경/누락 시 게시 정책과 F13 제외·재등장 시 자동 승인 차단·새 검수 버전·롤백·잠금·최소 수집 권한·열 단위 재검수 읽기 권한과 실제 CLI를 가상 자료로 검증한다. 기존 실제 게시 정책의 연결 누락 격리는 별도 구버전 테스트 DB에서 확인한다. 운영 DB·실제 사람 검수는 포함하지 않는다.

0005 수집 저장 검사는 별도 임시 `_test` DB에서 완전한 생성본의 적재·변경 이력·재적재·누락·재등장·역전 방지·롤백과 권한 경계를 확인한다. 원천 응답은 가상 자료이며 실제 정책 사실 검증과 구분한다. [설정·실패 복구](../src/benefitfinder/infrastructure/database/COLLECTED_POLICIES.md)를 따른다.

0004 ECOS 금리 검사는 별도 임시 `_test` DB를 만들고 제거한다. 가상 금리의 저장·재시작·갱신 역전 방지·실패 시 이전값 보존·제약·읽기 전용 권한·메타 API 연결을 확인한다. 실금리 수집과 운영 스케줄 검증은 [별도 범위](../src/benefitfinder/infrastructure/database/ECOS_RATES.md)다.


0001 F13 조항과 0002 게시 정책 카탈로그를 같은 전용 테스트 DB에서 검증한다. 카탈로그 검사는 가상 승인 자료의 게시·추천·상세 연결, 버전 전환·원자적 롤백, 승인 해시 불일치, 읽기 권한, 잠금 장애를 추가로 확인한다. 실제 정책 게시를 수행하지 않는다. [카탈로그 실행 안내](../src/benefitfinder/infrastructure/database/POLICY_CATALOG.md)를 참고한다.

이 디렉터리는 DB 없이 실행하는 `scripts/check.py` 기본 검사와 별개다. 실제 PostgreSQL 17에서 실행하며 SQLite나 메모리 테스트로 대체하지 않는다. CI는 PostgreSQL 17 서비스에서 같은 검사를 별도 필수 단계로 실행한다.

비어 있는 **전용 `_test` DB**를 준비하고 관리 역할의 연결 문자열을 `F13_TEST_DATABASE_URL`에 설정한다. 테스트는 이 DB의 F13 테이블을 반복 초기화하므로 실제 자료를 넣지 않는다. 값이나 DB 덤프를 공유 파일에 저장하지 않는다.

```sh
uv sync --locked
uv run --locked ruff check backend/integration
uv run --locked python -m unittest discover -s backend/integration -v
```

미설정·잘못된 연결·`_test`가 아닌 DB는 실패한다. 테스트를 skip하지 않는다. 문서 3개·조항 6개의 가상 취업·주거 사례를 넣어 마이그레이션의 원자성·중복 적용 실패·제약, 동일 정책/버전/분류와 최대 5개, 상태별 제외, 버전 변경의 즉시 반영, 실제 접속 실패·잠금 제한, API 근거 메타데이터와 계산 보존, SELECT 전용 역할의 읽기/쓰기 권한을 확인한다. 테스트 역할 `f13_test_reader`와 공개 가상 암호 `fictional-test-only`는 전용 테스트 DB/클러스터에서만 사용한다.

macOS에서 PostgreSQL 17 실행 파일을 PATH에 둔 경우 별도 로컬 클러스터 준비 예다. 기존 데이터가 있으면 `initdb`는 실패하며 덮어쓰지 않는다. 소켓은 현재 사용자만 접근하고 TCP는 localhost에서 암호 인증을 요구한다.

```sh
mkdir -p data/local/f13-postgres /private/tmp/bf-f13-17
chmod 700 /private/tmp/bf-f13-17
initdb -D data/local/f13-postgres/pgdata --no-locale -E UTF8 \
  -U f13_test_owner --auth-local=trust --auth-host=scram-sha-256
pg_ctl -D data/local/f13-postgres/pgdata -l data/local/f13-postgres/postgres.log \
  -o '-k /private/tmp/bf-f13-17 -h 127.0.0.1 -p 55432' -w start
createdb -h /private/tmp/bf-f13-17 -p 55432 -U f13_test_owner benefitfinder_f13_test
export F13_TEST_DATABASE_URL='postgresql://f13_test_owner@/benefitfinder_f13_test?host=/private/tmp/bf-f13-17&port=55432'
uv run --locked python -m unittest discover -s backend/integration -v
```

화면 검증용 가상 사례만 다시 준비하려면:

```sh
uv run --locked python -m backend.integration.postgres_fixture
```

이 명령은 테스트 DB의 F13 테이블만 초기화하고 기존 메모리 예제와 같은 가상 사례를 넣는다. 실제 사람의 승인이나 운영 게시가 아니다. 준비 후 서버에 **읽기 전용 역할의** `DATABASE_URL`과 `F13_OLLAMA_MODEL=gemma4:e4b`를 전달해 `pnpm dev`로 실행한다. 파일의 환경 변수는 자동 로드하지 않는다. 시작 → 만 25세·구직·무주택 월세·취업/주거 관심 → 13단계 확인 → 분석 → 결과에서 두 정책의 AI 설명과 펼친 근거를 확인한다. 근거 위치는 `가상 안내 / ...`, 확인일은 2026-11-07이며 정책 계산의 가상 출처 날짜와 별도로 전달된다.

위 검증에서 만든 읽기 역할을 사용하는 서버 실행 예다. Ollama가 실행 중이고 E4B 모델을 준비한 상태여야 생성 설명을 표시한다. 모델 미준비·지연이면 기존 5초 제한과 기본 안내를 적용한다.

```sh
export DATABASE_URL='postgresql://f13_test_reader@/benefitfinder_f13_test?host=/private/tmp/bf-f13-17&port=55432'
export F13_OLLAMA_MODEL=gemma4:e4b
pnpm dev
```

검증 후 테스트 클러스터만 중지하려면 `pg_ctl -D data/local/f13-postgres/pgdata -m fast -w stop`을 실행한다.

선행 PR #16의 F13 코어·화면과 PR #19의 실제 지역 API가 함께 포함되어 있다. 13단계 검증은 공식 지역 API를 사용하며 가상 지역 목록으로 대체하지 않는다. 지역 조회가 실패하면 재시도한 뒤 검증한다. 정책·금액·게시 조항은 위 전용 테스트 DB의 가상 자료다.

원문·DB 데이터 디렉터리·검증 요청/결과·로그·캡처는 `data/local/f13-postgres/` 같은 Git 제외 경로에 둔다. 결과 화면의 정책·금액·근거는 가상 검증 자료이며 실제 정책 신청 정확성은 실제 게시 자료 연결 후 별도로 검수한다.

## 실제 F12 승인 근거의 미게시 검증

`backend.integration.f12_review`는 명시적으로 실행하는 로컬 검증 도구다. production
`benefitfinder.main`은 이 모드를 활성화하지 않는다. 로컬 호스트/Unix 소켓의 이름이 `_test`로
끝나는 별도 DB만 허용한다. 가상 fixture 검사는 별도 가상 DB에서 실행하고, 실제 승인 자료가
있는 DB에 `seed_test_database`를 실행하지 않는다.

화면 검증용 `serve`는 실제 지역 API PR #19가 포함된 결합 브랜치에서 실행한다. 지역 구현이
없으면 시작을 거부하며 가상 목록으로 대체하지 않는다. 파서·DB 검사와 import는 #19 없이도 실행된다.

F12 PR #14에서 사람이 명시한 항목별 승인·반려 및 범위를 기록하고 `export-approved`로
해당 후보/원문 해시에 연결된 승인 항목만 내보낸다. 내보내기 파일은 인증된 서명이 아니므로
실제 검수 기록과 사유를 함께 확인한다. 이 도구는 새 승인을 만들지 않는다.

빈 전용 DB를 관리자가 따로 준비한 뒤 실행한다. 연결 문자열은 환경 변수로만 전달한다.

```sh
# F13_REVIEW_TEST_DATABASE_URL: 로컬 전용 *_test DB의 관리 역할
uv run --locked python -m backend.integration.f12_review migrate
uv run --locked python -m backend.integration.f12_review import-approved \
  --document employment '가상 취업 정책' data/local/reviews/employment/approved-export.json \
  --document housing '가상 주거 정책' data/local/reviews/housing/approved-export.json
```

`migrate`는 versions/0001과 [검증 출처 스키마](../migrations/testing/0001_f12_review_imports.sql)를
한 트랜잭션에서 명시적으로 적용하며 기존 스키마를 덮어쓰지 않는다. import도 한 트랜잭션이며
기존 버전/항목 충돌은 전체 롤백한다. 문서·조항은 **draft, published_at=NULL**로 두고 기존
검수자·검수일·사유·후보·원문/쌍/내보내기 해시를 보존한다. F12 원본 정책 ID를 보존하며 API
ID에만 `youth:`를 붙인다. 가상 P1/P3/P6로 치환하지 않는다. 공고 전문과 반려·대기 항목은 넣지
않는다. 기본 `PostgresPublishedClauses` 게시 조회는 이 자료를 0건으로 조회한다.

같은 원문 버전에 사람이 추가 승인한 항목을 넣을 때만 `import-approved --append`를 명시한다.
추가 내보내기에는 **새 항목 ID만** 넣는다. 기존 문서의 이름·분류·출처·확인 시각 및 검증 출처가
일치해야 재사용하며, 기존 항목·승인·해시는 갱신하거나 삭제하지 않는다. 중복 항목, 문서 정보나
상태 불일치는 전체 배치를 롤백한다. 원문이 같은데 추가 적재를 위해 버전을 임의로 올리지 않는다.
기본 import의 중복 거부 동작은 유지한다. 기존 승인 자료가 있는 DB에서 migrate나 가상 seed를
다시 실행하지 않는다.

```sh
uv run --locked python -m backend.integration.f12_review import-approved --append \
  --document employment '가상 취업 정책' data/local/reviews/employment/new-approved-export.json
```

서버에는 별도 SELECT 전용 역할을 사용한다. 관리 역할에서 해당 역할에 public 스키마 USAGE와
`f13_policy_documents`, `f13_clauses`, `f13_test_review_imports`의 SELECT만 부여한다.

```sh
# F13_REVIEW_TEST_DATABASE_URL: 위 DB의 SELECT 전용 역할
uv run --locked python -m backend.integration.f12_review serve --model gemma4:e4b
# 다른 터미널
pnpm dev:web
```

새 승인 근거를 화면에서 개별 검증하려면 서버에 `--focus-item POLICY_ID ITEM_ID`를 지정한다.
예: `serve --model gemma4:e4b --focus-item youth:virtual-approved eligibility:2`.
해당 정책·버전의 지정한 미게시 승인 항목만 조회하고 그 승인 범위만 보여 준다. 항목이 없거나
승인/미게시 상태를 벗어나면 다른 근거로 대신하지 않는다. 여러 정책은 옵션을 반복하고 한 정책에는
하나의 항목만 지정한다. 사용자 입력이나 공개 추천 API에 근거 선택 파라미터를 추가하지 않는다.

같은 정책에 다른 공식 지침을 추가할 때는 해당 문서의 URL·버전·해시로 별도 승인 내보내기를
적재한다. 기존 공고의 문서 정보나 인용을 교체하지 않는다. 정책에 미게시 문서 버전이 둘 이상이면
서버 시작 시 `--focus-document POLICY_ID DOCUMENT_VERSION`으로 하나를 명시해야 한다.
예: `serve --focus-document youth:virtual-approved sha256:<보충문서해시> --focus-item youth:virtual-approved eligibility:1`.
알 수 없는 버전·중복 지정·선택 없는 다중 버전은 시작을 거부한다. 선택한 문서/항목이 이후
사라지거나 승인 해제되어도 다른 버전의 같은 항목 번호로 대신하지 않는다. 이 옵션도 로컬 검증
서버 설정이며 공개 API나 사용자 입력에는 추가하지 않는다.

13단계는 production 공식 지역 라우터/어댑터를 사용한다. 이 검증용 추천 API는 관심 분야의 실제
정책을 `not_yet_reviewed`에 표시한다. 정책 목록은 시작할 때 DB에서 읽은 버전으로 고정하고,
근거와 승인 범위는 매 요청 DB에서 새로 읽는다. 승인 근거가 없어지면 정책도 응답에서 제외한다.
같은 정책/버전/분류의 미게시·승인 조항과 검증 출처가
일치해야 한다. 최대 5개를 조회하고 지급 조건 우선의 한 조항으로 설명해 서로 다른 승인 범위를
섞지 않는다. published 플래그는 false다. 일반 게시 조회와 분리된 경로다.
검색/모델을 포함한 5초 제한·실패 시 기본 안내를 적용하고 인용·출처·승인 범위를 표시한다.

**자격은 확인 전, 금액 null, 조합 없음, 계산 unavailable**로 유지한다. 모델에는 프로필·부모·
특수조건·소득/재산을 보내지 않는다. 사업 개요 승인으로 상세 자격을 확정하거나 정기 지급일
승인으로 최초 지급을 추정하지 않는다. 운영 게시나 실제 수급 가능·금액 계산의 검증이 아니다.

기존 PostgreSQL 검사와 추가 검사는 다음 명령으로 실행한다. 추가 검사는 별도 임시 DB와 가상
승인 자료만 사용한다. 검사 변수 `F13_TEST_DATABASE_URL`과 실제 승인 자료 검증 변수
`F13_REVIEW_TEST_DATABASE_URL`을 구분한다. 원문·기록·응답·로그·캡처는 로컬 제외 경로에만 둔다.

```sh
uv run --locked python -m unittest discover -s backend/integration -v
```

## 공유 분석 이용량

`test_analysis_usage_postgres.py`는 같은 전용 테스트 연결에서 별도의 UUID `_test` DB를 생성하고 0001·0003만 적용한 뒤 삭제한다. 125개 경합 요청의 정확한 100회 승인, 서로 다른 API 인스턴스의 추천·상황 변화 공유, 재시작 유지·이전 날짜 분리·비차감 조회·스키마 제약·접속/잠금/권한 실패를 검증한다. 집계에만 SELECT·INSERT·UPDATE를 가진 역할의 성공과 정책 문서 읽기 거부도 확인한다. 실제 사용자 조건·정책 자료는 넣지 않는다. 전체 discover 명령과 CI에서 함께 실행한다. 운영 적용은 [별도 설정 안내](../src/benefitfinder/infrastructure/database/ANALYSIS_USAGE.md)를 따른다.

## F12 작업 큐 (0007)

`test_policy_analysis_queue_postgres.py`는 별도 UUID 테스트 DB에서 등록 멱등성·동시 작업 확보·임대 만료·최대 재시도·원본 변경/누락/재등장·손상 입력·원천 읽기만 허용된 작업 역할을 검증한다. 0001·0002·0005·0006·0007을 적용하며 가상 자료만 사용한다. 전체 연동 검사와 마찬가지로 `F13_TEST_DATABASE_URL`은 별도 `_test` DB를 지정해야 한다. 실제 모델은 별도 GPU 실행으로 검증하고 실제 사람 검수와 구분한다.

## F14 게시 서비스

`test_published_services_postgres.py`는 전용 UUID `_test` DB에 0001·0002·0005·0006·0008을 적용한다. 서비스/현금 분리·승인 해시·별도 가격 근거·버전 전환/롤백·원천 변경·읽기 전용 역할·실제 HTTP 계약·게시 CLI를 가상 자료로 검증한다. F12의 0007과 독립적이며 실제 정책/단가의 사람 검수는 포함하지 않는다.

## 보조금24 소스 검증

`test_gov24_workflow_postgres.py`는 가상 자료와 UUID 전용 DB에서 0009까지 명시적으로 적용한다. 같은 원천 ID의 소스 분리, F12 안전 투영/재사용/변경 무효화, 두 원천의 동시 변경과 연결 카탈로그·F13 게시 회수·재검수 생성을 검사한다. 실제 API 수집·실제 정책 승인 검증과 구분한다.
