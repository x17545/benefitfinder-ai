# F13 실제 PostgreSQL 검증

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

선행 PR #16은 F13 코어·화면, 별도 PR #19는 실제 지역 API다. #16 미병합 동안 이 작업 브랜치는 그 커밋을 선행 의존성으로 포함한다. #19는 이 브랜치에 포함하지 않으므로 main에 지역 API가 없는 시점의 13단계 검증은 화면에 명시된 개발용 가상 지역을 사용한다. 실제 지역 연결 완료와 혼동하지 않는다.

원문·DB 데이터 디렉터리·검증 요청/결과·로그·캡처는 `data/local/f13-postgres/` 같은 Git 제외 경로에 둔다. 결과 화면의 정책·금액·근거는 가상 검증 자료이며 실제 정책 신청 정확성은 실제 게시 자료 연결 후 별도로 검수한다.
