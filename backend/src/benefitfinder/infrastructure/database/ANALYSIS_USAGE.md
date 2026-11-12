# 추천·상황 변화의 공유 일일 이용량

추천과 상황 변화를 합쳐 하루 100번의 분석 시작을 허용한다. 날짜 경계는 자정 KST다. 요청 스키마·분야·시나리오의 HTTP 검증이 끝나면 분석 전에 한 번 예약하며, 한도 초과는 스트림 시작 전 HTTP 429 `daily_limit_exceeded`다. 분석이 시작된 뒤 실패·시간 초과·연결 취소가 발생해도 되돌리지 않는다. 재시도는 새로운 한 번이다. 비싼 분석이 실행된 뒤 취소를 반복해 한도를 우회하지 않게 하는 처리다.

`ANALYSIS_USAGE_DATABASE_URL`을 설정하면 PostgreSQL의 `public.analysis_daily_usage`에 날짜와 횟수만 저장한다. 모든 프로세스·서버가 같은 DB를 사용해야 하나의 100회 한도를 공유한다. DB의 현재 시각을 KST 날짜로 바꾸고 원자적 UPSERT로 예약한다. 프로필·IP·사용자·요청 ID·정책·프롬프트를 저장하지 않는다. `remaining()`과 `GET /api/v1/meta` 조회는 차감하지 않는다. 메타 API의 데이터 준비·오류 기준은 [HTTP API 안내](../../api/README.md#서비스-메타데이터)를 따른다.

URL이 없을 때는 키·DB 없이 실행하는 **단일 프로세스 개발 예제**의 메모리 카운터를 사용한다. 프로세스를 재시작하면 초기화되며 여러 worker·서버의 전역 상한을 보장하지 않는다. 운영과 여러 프로세스 실행에서는 PostgreSQL 설정이 필요하다. 설정한 DB의 장애·잠금·권한·마이그레이션 누락은 HTTP 503 `dependency_unavailable`이며 메모리 카운터로 전환하지 않는다. 서버 시작은 연결·DDL을 수행하지 않는다. `.env` 파일은 자동으로 읽지 않는다.

## 적용과 최소 권한

관리 역할에서 [0001](../../../../migrations/versions/0001_f13_published_clauses.sql) 이후 [0003](../../../../migrations/versions/0003_analysis_usage.sql)을 명시적으로 적용한다. 0002는 PR #34의 정책 카탈로그 이력이며 이용량 테이블과 의존성이 없다. 이미 적용한 SQL을 다시 적용하지 않는다. 실제 운영·공유 DB 적용은 별도 승인이 필요하다.

```sh
psql "$DATABASE_URL" -X --set=ON_ERROR_STOP=1 --single-transaction \
  --file=backend/migrations/versions/0003_analysis_usage.sql
```

F13의 SELECT 전용 연결과 분리해 이용량 표에만 쓰기 권한을 가진 역할을 전달한다. 역할·자격 증명 생성은 환경 관리자가 담당한다.

```sql
GRANT USAGE ON SCHEMA public TO benefitfinder_usage;
GRANT SELECT, INSERT, UPDATE ON public.analysis_daily_usage TO benefitfinder_usage;
```

```sh
export ANALYSIS_USAGE_DATABASE_URL='postgresql://<usage-user>:<password>@<host>/<database>'
pnpm dev
```

공용 검사에서는 메모리 카운터의 동시 요청·KST 자정, 두 HTTP 경로의 공유·오류·검증 전 차감 금지를 확인한다. [별도 PostgreSQL 통합 검사](../../../../integration/README.md)는 가상 데이터의 독립 `_test` DB에서 여러 저장소 인스턴스의 경합·재시작 후 유지·DB 실패를 검증한다. 날짜별 익명 집계만 보관하며 오래된 집계의 정리 정책은 운영 구성 시 정한다.
