# ECOS 기준금리

기준금리는 사용자 요청에서 외부로 조회하지 않는다. 별도 갱신 명령이 한국은행 공식 API를 읽고 마지막 성공값을 PostgreSQL에 저장한다. 추천·상황 변화·메타 API는 같은 저장소를 읽으며, 한 번의 분석 안에서는 하나의 금리로 모든 기간·전후 결과를 계산한다. 가상 정책 모드는 기존 가상 금리와 미확인 상태를 유지한다.

## 공식 수집 계약

[ECOS Open API](https://ecos.bok.or.kr/api/)의 `StatisticSearch`에서 통계표 `722Y001`, 항목 `0101000`, 일별 주기 `D`를 조회한다. [공개 항목 응답](https://ecos.bok.or.kr/api/StatisticItemList/sample/json/kr/1/10/722Y001)에서 ‘한국은행 기준금리’, 일별 날짜 형식과 `연%` 단위를 확인했다. 최근 31일의 행을 받아 `TIME`이 가장 큰 관측치를 사용하고, `DATA_VALUE`를 100으로 나눠 연 비율로 저장한다. 관측일은 해당 일별 통계의 기준일이며 금리 변경 결정일을 뜻하지 않는다. 실제 금리 수치는 코드·가상 fixture에 넣지 않는다.

다른 통계·항목·단위, 빈 목록, 잘린 결과, 미래 일자, 상충 값, 비정상 JSON·숫자, HTTP 또는 본문 오류는 실패다. 키는 URL 경로에 들어가므로 서버 환경에만 두고 요청 URL·원문·예외 세부를 출력하지 않는다. HTTPS 고정 호스트를 사용하며 리디렉션을 따르지 않는다.

## 적용과 실행

관리 연결로 `0001` 이후 다음 파일을 명시적으로 적용한다. 시작 시 자동 DDL·초기 금리 삽입은 없다. 공유·운영 DB 적용은 별도 승인 대상이다.

```sh
psql "$DATABASE_URL" -X --set=ON_ERROR_STOP=1 --single-transaction \
  --file=backend/migrations/versions/0004_ecos_base_rate.sql
```

`ECOS_API_KEY`와 `BASE_RATE_WRITE_DATABASE_URL`을 작업 프로세스의 환경에 설정한 뒤 실행한다. `.env`는 자동으로 읽지 않는다. 실제 값은 Git에 넣지 않는다.

```sh
uv run --locked python -m benefitfinder.jobs refresh-ecos-rate
```

성공한 응답만 한 트랜잭션으로 교체한다. 실패하면 종료 코드 2로 알리고 이전 성공값·관측일·확인 시각을 보존한다. 더 오래된 관측일이나 확인 시각이 늦게 도착해도 최신값을 덮어쓰지 않는다. 같은 관측일의 공식 정정은 더 새로운 확인 시각으로 반영할 수 있다.

API에는 `BASE_RATE_DATABASE_URL`을 설정한다. 실제 게시 정책 계산에는 `POLICY_DATABASE_URL`도 필요하다. 미설정·빈 금리 테이블·금리 조회 실패는 `base_rate: null`, `unknown`이며 현재가치만 `unavailable`로 둔다. 명목금액과 자격은 보존한다. 갱신 작업 실패 뒤 읽을 수 있는 이전 성공값은 계속 사용하며, 확인 시각을 오늘로 바꾸지 않는다.

실행 역할은 기존 DB 소유자와 구분한다. 아래 역할 이름은 설정 예시이며 역할 생성·로그인 비밀값은 환경 관리자가 정한다.

```sql
GRANT USAGE ON SCHEMA public TO benefitfinder_rate_reader, benefitfinder_rate_writer;
GRANT SELECT ON public.ecos_base_rate TO benefitfinder_rate_reader;
GRANT SELECT, INSERT, UPDATE ON public.ecos_base_rate TO benefitfinder_rate_writer;
```

금리 역할에 F13 문서·카탈로그·이용량 표 권한을 추가할 필요가 없다. API의 읽기 역할에 쓰기·DDL 권한을 주지 않는다. 새 런타임 의존성은 추가하지 않았다.

## 검증 범위와 남은 작업

공식 API의 공개 샘플로 항목·응답 형식을 확인했다. 단위 검사는 가상 HTTP 응답으로 단위 변환·최신 선택·실패·비밀값 비노출을 확인하고, 별도 PostgreSQL 검사는 마지막 성공값 보존·재시작·잠금·제약·최소 권한과 API 조회를 검증한다. 실제 정책 검수·게시 완료를 의미하지 않는다.

단독 `refresh-ecos-rate`는 수동 갱신이다. [F08 통합 실행](../../jobs/DAILY_REFRESH.md)의 `refresh-data`는 정책·금리를 함께 실행하고 단일 Discord 보고·03:00 스케줄 설정 생성을 제공한다. 운영 키·역할 설정과 실제 등록·배포 환경의 연속 실행 검증은 후속이다. 스케줄을 등록하지 않은 환경을 매일 자동 갱신 중이라고 표시하지 않는다.
