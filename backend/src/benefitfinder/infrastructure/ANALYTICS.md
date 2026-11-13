# F11 분석 이벤트 전달

브라우저는 Google Analytics 스크립트를 읽지 않고 분석 쿠키나 저장소도 쓰지 않는다. 웹 클라이언트가 공용 계약에 맞는 허용 이벤트만 백엔드로 보내고, 백엔드가 GA4 Measurement Protocol로 중계한다. 사용자 프로필 값, URL, 오류 원문, 화면 문구는 수집하지 않는다. `GET /api/v1/analytics/config`는 `{"enabled": boolean}`만 공개하며 측정 ID와 API 비밀키는 응답, 브라우저 코드, 로그, 예외에 넣지 않는다. 두 분석 API 응답은 `Cache-Control: no-store`를 쓴다.

## 설정과 시작 동작

분석 전달은 기본으로 꺼져 있다. 서버 환경에 아래 세 값이 모두 있을 때만 켠다.

```text
GA4_ENABLED=true
GA4_MEASUREMENT_ID=G-0000000000
GA4_API_SECRET=<server-only-measurement-protocol-secret>
```

`GA4_ENABLED`가 없거나 빈 값 또는 `false`이면 측정 ID·비밀키가 남아 있어도 전달하지 않는다. 그 밖의 값은 오설정으로 보고 `invalid_analytics_configuration`이라는 고정된 `ValueError`로 시작을 중단한다. `true`인데 측정 ID가 `G-` 형식이 아니거나 비밀키가 없거나 잘못된 경우도 같은 고정 오류로 닫힌다. 원래 환경값은 오류에 포함하지 않는다. `.env` 파일은 자동 로드하지 않으므로 배포·실행 환경이 서버 프로세스에 값을 전달해야 한다.

## API와 변환

`POST /api/v1/analytics/events`는 공용 `analytics-event` 계약의 17개 이벤트만 받는다. 최상위 객체와 이벤트 객체 모두 추가 필드를 금지하고, 정수와 불리언은 엄격한 JSON 자료형으로 검사한다. `client_id`는 소문자 UUIDv4 한 형식, `session_id`는 `1..9999999999`만 허용한다. 검증 실패는 기존 공통 오류 형식의 `422 invalid_request`이며 입력 원문을 되돌려 주지 않는다. 성공 응답은 본문 없는 `204`다.

GA4는 이 프로젝트의 UUID를 `client_id`로 받아들이지 않으므로 어댑터에서 UUID 전체 128비트를 상위 64비트와 하위 64비트의 십진수로 바꿔 `<upper>.<lower>`로 보낸다. 예를 들어 `00000000-0000-4000-8000-000000000001`은 `16384.9223372036854775809`다. 원래 UUID는 `user_id`나 다른 필드로 함께 보내지 않는다. 이벤트별 허용 스칼라 필드만 다시 검사해 복사하고 `session_id`와 고정 `engagement_time_msec: 1`을 더한다. `apply_click.rank`의 계약상 `null`은 알 수 없음으로 유지하되 GA가 null을 거부하므로 전송 params에서만 생략한다. `0`이나 임의 순위로 바꾸지 않는다.

전송 JSON은 최소 형태의 `client_id`, `events[].name`, `events[].params`, 그리고 다음 동의 상태만 포함한다.

```json
{
  "consent": {
    "ad_user_data": "DENIED",
    "ad_personalization": "DENIED"
  }
}
```

광고 식별자, `user_id`, `user_properties`, 프로필, 기기·지역 정보는 만들거나 전달하지 않는다. 요청 IP, User-Agent, Cookie, Referer, 경로, 오류 문장도 어댑터로 복사하지 않는다. 목적지는 코드에 고정된 `https://www.google-analytics.com/mp/collect` 하나이며 사용자·환경 변수로 host를 바꿀 수 없다. 직접 HTTPS 연결을 쓰고 redirect를 따라가지 않는다.

## 실패와 한도

전달은 best effort다. 외부 호출은 동시에 최대 4건, 한 건당 1초로 제한하고 응답 상태줄만 제한 길이로 읽는다. 용량을 넘은 이벤트는 기다리지 않고 버린다. 재시도, 대기열, 디스크·DB 저장, 백그라운드 무한 작업은 두지 않는다. 비활성 상태에서는 외부 연결을 만들지 않고, 연결·TLS·응답·시간 초과를 포함한 어떤 전달 실패도 이벤트 API를 5xx로 바꾸지 않는다. 수집 성공 여부를 사용자 흐름의 성공 조건으로 쓰지 않는다.

Google 측의 `204`는 요청 접수 응답이며 실제 보고서 집계를 보장하지 않는다. 자동 테스트는 가짜 transport로 고정 URL, 최소 JSON, 금지 필드 부재, UUID 변환, null 순위 생략, 실행 한도를 확인하며 실제 Google에는 요청하지 않는다.

[Google 공식 Measurement Protocol 안내](https://developers.google.com/analytics/devguides/collection/protocol/ga4)도 MP만으로 보내는 이벤트가 partial reporting을 포함할 수 있다고 명시한다. 이 구현의 범위는 기획 6.6의 허용 이벤트 집계와 `step_view`/`step_complete`를 이용한 단계별 이탈 확인이다. 쿠키 없는 임시 ID를 쓰므로 같은 사람의 재방문 연결, 광고 유입 attribution, 기기·지역 보고서의 완전성을 보장하지 않는다. 따라서 분석 쿠키·서드파티 스크립트 없이 이벤트를 중계하는 기능의 범위이며, GA 전체 보고서 연결이 끝났거나 모든 사용자·유입 경로를 측정한다고 표시하지 않는다. 실제 계정의 이벤트 수신과 팀원 보기 권한도 별도로 검증되지 않았다.

별도의 Google debug 검증에서는 가상 측정 ID `G-0000000000`과 가상 비밀키 `fictional-test-only`, `ENFORCE_RECOMMENDATIONS`를 사용해 17개 기본 이벤트가 `HTTP 200`, 빈 `validationMessages`를 받았고 `apply_click.rank: null`은 전송에서 제외해야 함을 확인했다. 이 결과는 페이로드 형식 검증일 뿐 실제 자격 증명의 유효성, 운영 전달·보고서 집계, 정책 또는 사용자 데이터 검증, 실제 계정 연결을 뜻하지 않는다. debug endpoint도 API 비밀키의 실제 유효성을 판별하지 않는다.

2026-11-14에는 실제 웹 전송 모듈을 Node에서 실행해 로컬 Uvicorn의 두 API와 GA 어댑터까지 연결했다. 외부 전송만 가상 transport로 대체해 17개 이름과 상세 화면의 순위 미상 분기까지 요청 18개를 대조했다. 프로필을 끼워 넣은 추적 호출은 전송되지 않았고, API에 직접 보낸 추가 프로필 필드는 입력을 노출하지 않는 422로 거부됐다. 이때 어댑터가 만든 실제 페이로드 18개를 Google debug 서버에 전달한 최종 형식 검증도 빈 `validationMessages`로 통과했다. 운영 수집 endpoint와 실제 계정은 사용하지 않았다.
