# 보조금24 공식 API 연결

[공공데이터포털의 대한민국 공공서비스 정보](https://www.data.go.kr/data/15113968/openapi.do)와 연결된 [공식 Swagger](https://infuser.odcloud.kr/api/stages/44436/api-docs)를 기준으로 한다. 목록·상세·지원조건 세 API를 동일 서비스 ID로 조회한다. 웹페이지·첨부파일을 크롤링하지 않는다. 실제 키를 사용한 수집 성공이나 정책 사실 검수 완료를 뜻하지 않는다.

## 먼저 확인할 매핑

[F12 입력 계획](F12_PREPARATION.md)의 형식에 `"source": "gov24"`를 추가한다. 생략한 기존 계획은 온통청년이다. 취업·주거에 해당하는 실제 `서비스분야` 값과 정책 ID·정식 이름을 사람이 확인해야 한다. 분류값·제목의 단어·연령의 누락으로 범위를 추정하지 않는다. 아래 분류와 ID는 모두 가상이다.

```json
{
  "format_version": 1,
  "source": "gov24",
  "scope": {
    "category_field": "serviceCategory",
    "category_values": {
      "employment": ["fictional-employment"],
      "housing": ["fictional-housing"]
    }
  },
  "policies": [{
    "id": "youth:fictional:A",
    "name": "가상 청년 취업 A",
    "categories": ["employment"],
    "aliases": []
  }],
  "targets": [{
    "policy_id": "youth:fictional:A",
    "documents": [{
      "external_id": "A",
      "name_field": "serviceName",
      "body_fields": ["detailTarget", "detailSupport", "ageStart", "ageEnd"],
      "scope_guards": {"serviceCategory": "fictional-employment"}
    }]
  }]
}
```

원천 ID는 최대 100개이며 대상 ID마다 세 API를 조회한다. `supportConditions`의 `JA0110`·`JA0111`은 확인된 정수 연령 시작·끝으로 받아 19~39세와 겹치는지 검사한다. 빈 값·뒤집힌 구간·미확인 분류 변경은 실패로 남겨 이전 성공본을 유지한다. 확인된 정상 연령 구간이 청년 범위에서 벗어나면 선택 범위의 누락으로 반영해 이전 게시를 회수한다. 다른 연령을 포함하는 정책을 청년에게 전부 적합하다고 판정하는 것은 아니다. 사용자 자격은 이후 검수한 규칙으로 판정한다.

본문은 원래 값을 그대로 보존한 세 객체 `serviceList`·`serviceDetail`·`supportConditions`로 저장한다. F12에는 명시적으로 선택한 안전한 별칭 필드만 투영한다. `serviceId`·`serviceName`·`serviceCategory`는 목록의 ID·이름·분류, `ageStart`·`ageEnd`는 지원조건의 연령이다. `detailPurpose`·`detailTarget`·`detailCriteria`·`detailSupport`·`detailMethod`·`detailDeadline`·`detailDocuments`·`detailLaw`·`detailLocalLaw`·`detailAdministrativeRule`은 상세의 목적·대상·선정기준·지원내용·신청방법·신청기한·구비서류·법령·자치법규·행정규칙이다. 연락처·문의처·외부 URL은 모델 입력에 투영하지 않는다.

## 실행

기존 0001~0008의 관련 테이블과 **0009_gov24_policy_sources**를 먼저 명시적으로 적용한다. [수집 쓰기 역할](COLLECTED_POLICIES.md#저장-경계와-권한)과 [F12 역할](F12_ANALYSIS.md#db와-실행)의 권한을 사용한다. 새 테이블·운영 DDL·자동 승인은 없다.

```sh
uv run --locked --env-file data/local/f08.env python -m benefitfinder.jobs \
  refresh-gov24 --plan data/local/gov24-preparation.json --data-dir data
uv run --locked --env-file data/local/f08.env python -m benefitfinder.jobs \
  ingest-gov24 --data-dir data
uv run --locked --env-file data/local/f08.env python -m benefitfinder.jobs \
  f12-analysis prepare --plan data/local/gov24-preparation.json
```

`GOV24_API_KEY`는 HTTP 어댑터만 사용하며 [공공데이터포털 인증 안내](https://www.data.go.kr/images/biz/swagger-guide/multi_swagger_guide.pdf)의 `Authorization: Infuser …` 헤더로 전송한다. 고정된 HTTPS 호스트/경로만 허용하고 리다이렉트를 따라가지 않는다. `COLLECTION_WRITE_DATABASE_URL`이 있으면 이전 파일 성공본의 DB 인계를 먼저 재시도하고 새 성공본도 DB에 인계한다. 미설정이면 로컬 파일만 만든다. `ingest-gov24`는 보존한 원본과 요청·해시를 재검사하며 API·LLM·Discord를 호출하지 않는다. `F12_WRITE_DATABASE_URL`은 준비·분석 큐에 필요하다.

일일 통합에는 `GOV24_PREPARATION_PLAN`에 계획 경로를 설정하고 기존 `refresh-data --notify-discord`를 실행한다. 설정한 보조금24·온통청년·ECOS를 독립 시도한 뒤 F12 입력 준비·분석·집계 알림을 실행한다. 일부 실패는 전체 실패로 기록하고 이전 전체 성공 시각을 유지한다. 미설정 보조금24를 수집 성공으로 표시하지 않는다. 실제 03시 스케줄 설치·운영 수집·Discord 도착은 별도 검증이다.

## 무결성과 검수 경계

- 원본은 `data/raw/f08-gov24/`, 성공본은 `data/processed/f08-gov24/`에 저장한다. 세 응답의 참조·해시를 묶은 manifest와 실제 원본을 DB 인계 전에 대조한다. 키·원문·정책 ID·예외 원문을 집계 알림에 넣지 않는다.
- 필터 조회의 건수는 `matchCount`다. 전역 `totalCount`를 1건 조회의 결과 수로 사용하지 않는다. 목록의 완전한 0건만 누락으로 취급하고 상세·지원조건 누락은 전체 실패다. 부분 응답·다른 ID·중복·해석 불가능한 값도 성공본으로 전환하지 않는다.
- `scope.json`은 한 데이터 경로의 ID·분류 매핑을 고정한다. 매핑 변경을 기존 정책 삭제로 오인하지 않도록 같은 경로에서 다른 범위로 수집하지 않는다. 범위 이동은 DB의 이전 소스와 연결 정책 영향을 검토한 명시적 데이터 이관으로 진행한다. 한 DB 소스에는 한 운영 수집 경로를 사용한다.
- 조회수만 바뀌면 내용 해시·F12 문서 버전을 유지한다. 상세·지원조건이 바뀌거나 정책이 사라지거나 확인된 연령이 청년 범위를 벗어나면 기존 연결 정책과 F13 게시를 회수하고 재검수 이력을 만든다. 연령/분야를 확인할 수 없으면 기존 성공본을 유지하되 실행을 실패로 기록하며 재확인이 필요하다.
- 동일 서비스 ID가 온통청년에도 있어도 서로 다른 원천이다. 실제 같은 정책인지는 공식 자료로 확인해 하나의 `youth:` 정책 ID와 복수 원천 연결로 검수한다. 이름만으로 합치지 않는다. 자동 준비에서 같은 정책을 두 계획에 중복 배정하지 않는다. 복수 원천을 한 분석에 함께 넣어야 하면 기존 명시적 `f12-analysis register --input`으로 두 원천 해시를 포함한다.
- 준비 결과는 검수 대기다. 사람의 별도 승인 파일 없이 계산용 카탈로그·서비스·F13에 게시하지 않는다. API 밖 첨부 공고의 내용·변경 감지와 실제 분류 매핑·검수 자료는 남은 항목이다.
