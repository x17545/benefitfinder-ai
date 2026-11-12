# 수집 성공본의 PostgreSQL 인계

`refresh-youthcenter`와 `refresh-data`는 `COLLECTION_WRITE_DATABASE_URL`을 설정하면 전체 수집에 성공한 파일 생성본을 PostgreSQL에 인계한다. 미설정은 기존 파일 보관 모드다. 빈 문자열로 설정하면 인계 오류이며 성공으로 처리하지 않는다. 수동 `collect-youthcenter`의 부분 수집 결과는 이 경로로 적재하지 않는다.

이 저장소는 **검수 전 원천 자료**다. API 필드를 혜택 계약·자격·금액·중복 제한으로 임의 변환하지 않는다. 원천 목록에는 분야·연령 범위를 검수해야 하는 자료가 포함될 수 있으며 이 자료를 사용자 검색·추천·계산에 사용하지 않는다. 청년 취업·주거 범위 확인, 공식 공고 보완, F12 분석·사람 검수·게시를 별도로 거친다. 기존 게시 정책·F13 조항·승인 상태를 수정하지 않는다.

## 준비와 실행

관리 역할로 0001 다음에 `backend/migrations/versions/0005_collected_policy_generations.sql`을 별도 트랜잭션으로 명시적으로 적용한다. 공유·운영 DB에는 별도 승인 없이 적용하지 않는다. import·앱 시작·수집 명령이 영구 테이블 생성이나 마이그레이션을 자동 수행하지 않는다. 적재 트랜잭션 안의 세션 임시 테이블은 커밋 시 제거하며 전용 역할의 DB TEMP 권한이 필요하다.

```sh
psql "$DATABASE_URL" -X --set=ON_ERROR_STOP=1 --single-transaction \
  --file=backend/migrations/versions/0005_collected_policy_generations.sql
```

전용 적재 역할의 연결 문자열을 Git 제외 `data/local/f08.env` 또는 작업 환경의 `COLLECTION_WRITE_DATABASE_URL`에 설정한다. API 서버의 `POLICY_DATABASE_URL`·F13 `DATABASE_URL`과 구분한다. 실제 키·주소는 명령행 인자·Git·로그에 넣지 않는다. `.env`를 자동으로 읽지 않으며 비밀 파일 권한은 600으로 둔다.

```sh
uv run --locked --env-file data/local/f08.env python -m benefitfinder.jobs \
  refresh-data --data-dir data --notify-discord
```

DB만 복구하거나 같은 성공본을 다시 인계할 때는 다음 명령을 쓴다. 온통청년·ECOS API와 LLM을 호출하지 않으며 Discord 알림도 보내지 않는다. 수집 API 키 없이 실행할 수 있다.

```sh
uv run --locked --env-file data/local/f08.env python -m benefitfinder.jobs \
  ingest-youthcenter --data-dir data
```

독립 재적재의 종료 코드는 성공 0, 입력·원본·DB 처리 실패 1, 연결 환경 변수 누락·잘못된 CLI 인자 2다. 출력은 고정 오류 또는 집계 건수이며 정책 원문·ID·연결 문자열·예외 원문을 출력하지 않는다.

## 검증과 실패 복구

- `data/processed/f08-youthcenter/status.json`의 마지막 완전 성공본만 선택한다. 이후 수집 시도가 실패했어도 직전 완전 성공본은 재적재할 수 있다. 파일의 실행 ID·완료 시각·레코드 건수와 실제 활성 레코드 수를 확인한다.
- 각 활성 레코드의 ID·내용 해시·수집 시각을 보존 원본 응답과 비교한다. 원본 본문 SHA-256·공식 API 경로·비밀값 없는 요청 파라미터·원본 참조도 확인한다. 담당자 이름 제거·조회수 해시 제외는 기존 수집 규칙과 같다.
- 읽기는 선택한 데이터 디렉터리 안으로 제한하며 파일별 크기 제한·중복 JSON 키·비유한 수치·경로 이탈·원본 불일치를 거부한다. 이 검사는 로컬 무결성 확인이다. 편집 가능한 파일을 공식 출처 인증이나 사람 승인으로 해석하지 않는다.
- 수집의 OS 잠금을 보유한 상태에서 기존 성공본의 DB 인계를 먼저 재시도한 뒤 새 수집을 시작한다. 기존 인계가 실패하면 새 API 수집을 하지 않는다. 새 완전 성공본도 잠금을 해제하기 전에 인계하므로 병렬 수집이 로컬 성공본을 건너뛰게 하지 않는다.
- DB 적재는 짧은 단일 트랜잭션에서 실행·최신 원천 레코드·변경 이력을 함께 반영한다. 네트워크 수집·파일 읽기·LLM 호출 동안 DB 트랜잭션을 유지하지 않는다. DB의 직전 성공본과 비교하므로 파일에 기록된 변경 목록에 의존하지 않는다.
- 같은 실행 ID와 같은 배치 해시는 기존 결과를 반환하며 최신본·변경 이력을 중복 생성하지 않는다. 같은 ID의 다른 내용과 처음 보는 과거 완료 시각은 거부한다. 이미 인계한 과거 성공본을 재호출해도 최신본이 뒤로 돌아가지 않는다.
- DB 장애 시 직전 DB 성공본은 유지하고 종료 코드로 실패를 보고한다. 새 파일 성공본이 이미 만들어진 경우에는 그 파일과 원본을 보존한다. 파일 수집 성공과 DB 인계 성공은 별개이며, 다음 실행이나 `ingest-youthcenter`가 같은 완전 성공본으로 복구한다.

## 저장 경계와 권한

원본 응답 전문은 계속 `data/raw/f08-youthcenter/`에 보존한다. DB에는 담당자 이름을 제거한 레코드와 원본 참조·시각·내용 해시를 넣는다. 원본 파일의 이동·삭제는 인계 검증과 추후 근거 조회에 영향을 주므로 코드 실행 사본과 분리된 데이터 디렉터리를 사용한다.

| 테이블 | 보존하는 내용 |
| --- | --- |
| `public.collected_policy_runs` | 인계된 완전 생성본의 ID·완료 시각·배치 해시·활성 건수·변경 집계 |
| `public.collected_policies` | 원천 ID별 최신 필드·원본 참조·내용 해시·실제 수집 시각과 누락 감지 시각 |
| `public.collected_policy_changes` | 신규·변경·누락 이벤트와 해당 버전의 필드·근거 참조. 동일 내용의 조회수 변화는 새 이벤트를 만들지 않음 |

누락은 물리 삭제하지 않는다. 이후 다시 나타나면 신규로 추적하며 과거 변경 payload를 덮어쓰지 않는다. 수집 시각은 사람 검수일·게시일이 아니다. DB의 실행 집계도 정책의 자격·중복 수급 사실을 확정하지 않는다.

기존에 준비한 적재 역할에 부여할 권한 예다. 실제 DB·역할 이름에 맞춰 관리자가 명시적으로 적용한다.

```sql
GRANT CONNECT, TEMPORARY ON DATABASE benefitfinder TO benefitfinder_collection_writer;
GRANT USAGE ON SCHEMA public TO benefitfinder_collection_writer;
GRANT SELECT, INSERT ON public.collected_policy_runs, public.collected_policy_changes
    TO benefitfinder_collection_writer;
GRANT SELECT, INSERT, UPDATE ON public.collected_policies TO benefitfinder_collection_writer;
```

저장 모델·마이그레이션은 윤정찬·양승현이 함께 검토하고 탁서윤은 변경 자료를 F12에 인계하는 흐름을 검토한다. 적재 역할에 게시 정책·F13 조항의 쓰기 권한을 주지 않는다. 운영 확인용 읽기 역할은 수집 세 테이블의 SELECT만 사용한다. 앱의 게시 정책 읽기 역할에는 이 수집 테이블을 추가하지 않는다.

F12 자동 분석·재검수 대기 소비·원문 변경에 따른 기존 승인 해제·게시 전환, 보조금24, 운영 키로 전체 수집과 실제 예약 실행은 별도 범위다. 이 저장 성공이나 변경 이벤트 생성을 정책 사실 검증·검수 완료로 보고하지 않는다.
