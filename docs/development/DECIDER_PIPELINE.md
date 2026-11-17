# Decider 정책 추출·게시 파이프라인

2026-11-17 현재 코드와 실행 기록을 정리한다. 이 문서는 구현·검증 상태를 설명하며
[기능 명세](../product/REQUIREMENTS.md)의 수락 조건이나 사람 검수 목표를 바꾸지 않는다.
Decider는 데이터 준비 단계의 오프라인 추출기다. 사용자 추천 요청에서 호출하거나
LLM이 자격·금액·조합 순위를 덮어쓰는 경로가 아니다.

## API 1차 적재와 로컬 모델 2차 갱신

사용자 요청에 따라 초기 적재에서는 AI 검수를 호출하지 않는다. 공식 API 원본을
먼저 보존하고 로컬 Decider의 추출·의미 판단 결과를 후속 갱신한다. 이 경로는 기존
정형 우선 분석기와 별도인 `api-first-locate-decide-v2`이며 F12 분석 버전은 8이다.
금액·기간·자격·모집·중복·예외 6종 locate 뒤 추출 결과에 의존하는 choice/noul/score를
별도 요청한다. `fast=False`를 유지하며 정책 사이 문맥은 합치지 않는다.

```bash
# 각 명령의 --output은 같은 비공개 실행 디렉터리다.
# POLICY_PUBLISH_DATABASE_URL은 비공개 실행 환경에서 설정한다.
uv run --locked python scripts/api_decider_snapshot.py --output data/local/api-decider-run
uv run --locked --group decider python scripts/api_decider_batch.py \
  --output data/local/api-decider-run --batch-size 16 --max-seconds 3600
# 기본은 로컬 *_test PostgreSQL에 기록한다. 확인한 후 --publish로 운영에 반영한다.
uv run --locked python scripts/api_decider_update.py --output data/local/api-decider-run --once
uv run --locked python scripts/api_decider_update.py --output data/local/api-decider-run --publish --once
# 공식 지역 목록 JSON을 제공하고 원문에 명시된 실비 규칙을 별도 게시한다.
uv run --locked python scripts/api_decider_expense_prepare.py \
  --output data/local/api-decider-run --regions data/local/regions.json
# 먼저 F13_TEST_DATABASE_URL의 격리 DB에서 실행하고, 운영에는 --publish를 추가한다.
uv run --locked python scripts/api_decider_expense_publish.py --output data/local/api-decider-run
```

snapshot 명령은 DB에 보관된 완전한 공식 API 세대를 멱등 재적재한다. 새 API 조회가
필요하면 기존 F08 수집을 먼저 실행한다. 원본·모델 응답·좌표·질문/모델 버전·실행 상태는
Git 제외 경로에 보존한다. 중단 후 같은 디렉터리로 재개하며 실패는 최대 3회 시도한다.
반영은 16건 단위 트랜잭션과 원천 해시/기존 버전 비교로 보호한다. 기존 승인 정책과
서비스는 자동 덮어쓰지 않는다. F12의 기존 상태명 `needs_review`는 저장 형식이며,
이 실행에서 별도 AI 검수나 개별 사람 승인을 요청한다는 뜻은 아니다.

2026-11-17 실제 운영 원천 4,027건은 2분 45초에 재적재했다. 신규·변경 0건이며
기존과 같은 4,027건이다. 이 중 넓은 취업·주거 대상 1,944건을 실제 RTX 3060 12GB에서
약 26분 6초에 모두 분석했다. 나머지 2,083건도 원천 DB에는 보존한다. 본문 9개 필드는
겹치는 window로 모두 처리하고 정형 API 코드·날짜는 별도로 보존한다. 긴 문서를
조용히 자르지 않으며 모델 결과가 없다는 이유로 원본을 지우지 않는다. 분석 완료 수와
계산용 게시 수는 다르다. AI 정확도 평가로 이 수치를 사용하지 않는다.

`decider_expense_cash.compile_decider_expense_cash()`는 실제 locate와 후속 의미 판단에
연결된 API 문구에서 명시적 지원율·상한만 읽는다. 50% 실비는 비용의 절반과 상한 중
작은 금액으로 기존 계산기가 계산하며, 지출·청구월이 없으면 금액을 만들지 않는다.
계산 가능한 16건을 추가해 기존 63건에서 79건으로 늘렸고 실제 배포 API에서
65,000원 지출 → 32,500원, 250,000원 지출 → 상한 100,000원을 확인했다.
이는 가상 신청자 조건의 계산 결과이며 지급 확정이 아니다. 현재 웹의 기본 질문 흐름은
이 실비 입력을 수집하지 않으므로 해당 확인은 배포 HTTP API 직접 호출로 수행했다.
외부 API 연결과 일일 스케줄은 사용자 요청으로 보류한다.

## 기존 정형 우선 경로의 구현과 경계

- [pplx-decider-flash-v1.3](https://huggingface.co/perplexity-ai/pplx-decider-flash-v1.3)은
  Apache-2.0 공개 모델이다. 현재 코드는 revision
  `d06fb3f2d97e0a52325a0d463c450cf0338f44fe`의 manifest·파일 크기·SHA-256을 확인한
  snapshot만 로드한다. 내려받은 코드를 검증 전에 실행하지 않는다.
- 로컬·호스팅 공통 경로는 정형 필드 코드 처리 → 본문 3종 `locate` 한 번으로 바뀌었다.
  전문을 다시 보내는 `choice`·`noul`·`score` 후속 호출은 없다. 로컬 모델은
  `fast=False`, `locate=True`, `mode="separate"`를 유지한다.
- 공용 질문 버전은 `youth-decider-v5-structured-first`, F12 분석 버전은 7이다. 월세지원 실행 규칙 후보가 추가되어 큐를 구분하며 질문·모델 응답 캐시는 유지한다.
  기존 질문·응답 캐시와 v5 큐 결과를 새 결과로 재사용하지 않는다.
- F12 기본 엔진은 기존 `tools`다. 이 개선은 `F12_ANALYSIS_ENGINE=decider` 및
  `decider-batch run` 경로에 적용하며, 기존 도구형 분석을 자동 전환하지 않는다.
- Decider 의존성은 선택 그룹 `decider`에 있다. 기본 API·웹 설치를 무겁게 만들지 않으며,
  로컬 실행은 `uv run --locked --group decider ...`로 격리한다.
- 현재 추출 코드는 미배포다. 사용자가 승인한 초기 운영 DB 적재·계산용 게시는
  아래 실행 기록까지 반영했다. 외부 API 연결과 일일 스케줄은 사용자 요청으로 보류했다.

## 처리 흐름

1. 공식 취업·주거 범위를 코드로 선별하고 원천·보충 문서·버전을 보존한다.
2. **모델 호출 전** `prepare_source_fields()`가 연령·지역 코드·소득 원시값·취업/학력/
   혼인/특화 코드·명확한 신청 날짜를 읽는다. 코드 의미·누락·0을 구분한다.
   소득 숫자의 통화 단위가 확인되지 않으면 원화 상한으로 확정하지 않는다.
   전공 코드도 공식 코드정의서로 읽으며, 현재 프로필이 표현하지 못하는 전공 제한은
   모델 추측으로 풀지 않고 호출 전에 검수 보류한다.
3. 설명·지원 내용·추가 자격·소득 설명·참여 제한·기타 사항·신청 안내 등 본문을
   빠짐없이 별도 view로 만든다. 새로 들어온 알 수 없는 필드와 보충 문서 전문도 포함한다.
   정형값·지역 코드 나열·기관/URL metadata를 모델에 다시 보내지 않는다.
4. 본문 view만 겹치는 window로 나눠 `eligibility`·`exceptions`·`overlap` 3종을 찾는다.
   금액·기간·지급 시점과 신청 날짜는 코드 파서가 처리하고, 불명확하면 보류한다.
   새 view 좌표는 원본 문서 좌표로 복원한다. 원 모델 선택은 `raw_spans`, 코드가
   가구·단서 문맥을 보강한 근거는 `method=source_context`로 구분한다.
5. 모든 본문 필드를 `preparation.body_fields`와 정규화 결과에 보존한다. 모델이
   아무것도 찾지 않아도 본문이 사라지지 않는다. 본문 후보에는
   `body_rules_require_review`를 남겨 검수 없이 실행 규칙으로 승인하지 않는다.
   기존 원문·해시·신청 기간·금액/일정·중복 검증을 그대로 적용한다.

정형 필드만 있는 문서는 모델 호출 0회다. `DECIDER_MAX_POLICY_REQUESTS` 기본값 32,
`DECIDER_MAX_POLICY_CHARACTERS` 기본값 40000을 **정책의 모든 문서에 합산**해 호출 전에
검사한다. 초과하면 `decider_policy_analysis_budget_exceeded`로 전체 정책을 실패 처리하며
일부만 잘라 성공 처리하지 않는다. 미지 코드·중복 헤더·잘못된 숫자도 유료 분석 전에
검수 대상으로 분리한다. 공급자 오류·예산 초과를 batch 분할 재시도로 증폭하지 않는다.
호스팅 어댑터의 기존 일일 실제 API 호출 예산은 별도로 유지한다. 논리 요청 상한은
공급자별 청구 금액/토큰 상한을 뜻하지 않는다.

추출 artifact의 `preparation`은 정형 원시값/코드 해석, 전체 본문 필드/원문 좌표,
원문·본문 글자 수, 논리 요청 수, 질문 수 3, 후속 요청 수 0을 기록한다.
평가에는 `raw_spans`만 모델 성능으로 세며 빈 배열을 코드 근거로 대체하지 않는다.
과거 7종/5종 판단 기준의 평가 통과를 새 3종 모델의 검증으로 재사용하지 않는다.
아래 v1·v2·기존 대량 처리 수치는 이전 경로의 기록이다.

## 정형 우선 경로 재현 검증

실제 자료는 Git 제외 `data/local/structured-first-verification/`에만 둔다.
`records.json`은 담당자·연락처 필드를 제외한 실제 API 저장 필드, `manifest.json`은
기존 읽기 전용 공식 범위 선택기로 만든 분석 입력이다. 전체 분야 routing 감사는
MVP 범위를 확대하거나 정책의 게시·수급 가능 여부를 승인하는 작업이 아니다.

```bash
uv run --locked python scripts/verify_structured_analysis.py \
  --records data/local/structured-first-verification/records.json \
  --output data/local/structured-first-verification/audit.json

# 로컬 GPU만 사용. gold는 실제 원문에서 확인한 조항과 종류를 담는 비공개 파일이다.
uv run --locked --group decider python scripts/verify_structured_analysis.py \
  --manifest data/local/structured-first-verification/manifest.json \
  --gold data/local/structured-first-verification/gold.json --model-limit 2 \
  --checkpoint data/local/decider/checkpoint \
  --output data/local/structured-first-verification/model.json
```

모델의 전체 조항 추출에 누락이 있으면 검증 도구는 exit 2를 반환한다. 원문 전체 보존,
코드의 문맥 보강, 모델 자체의 완전한 추출은 각각 별도 지표다. 원문이 남아 있다는
이유로 모델 정확도 검사를 통과 처리하지 않는다. 표본 검증은 전체 정책 사실의 검수가 아니다.

2026-11-16 공용 투영에 전공·사업기간 코드를 추가한 뒤, 전체 실제 수집본 4,029건의
126,261개 투영 필드와 원본 JSON의 명시 조건 필드 88,576개 모두 전달 누락 0이었다.
확인할 전공 제한이 있는 187건은 모델 호출 전에 보류한다. 동일 window 900 기준
사전 계수에서 논리 요청은 13,225 → 4,340, 질문 평가는 92,575 → 13,020,
입력 state 글자 수는 기존 하한 12,470,199 → 1,817,513이었다. 이는 전체 원천을
대상으로 한 계수이며 MVP 범위에 전부 편입한 것이 아니다. 실제 API 청구액·토큰·처리 시간
측정도 아니다.

원본 JSON과 투영을 별도로 대조하는 `raw_condition_fields` 감사를 추가했다.
기존 투영에서 빠졌던 `plcyMajorCd`(전공 제한)와 `bizPrdSeCd`는 공용 투영 담당이
추가했으며, 새 corpus 1,435건과 hash를 `data/local/structured-first-verification-v6/`에
재생성했다. 이전 입력·실제 모델 응답·실측 비교는 원래 폴더에 보존했다.
전공 코드 정의서 SHA-256은 기존 검증값과 일치했다. 신규 코드/필드는 공식 자료와
대조하고, 모델에 코드 의미를 추측시키지 않는다.

실제 월세·면접 정책 2건을 같은 로컬 GPU/model/최대 길이 3072/batch 1에서
보존한 변경 전 코드와 새 경로로 각각 실행했다. 모델 로딩을 제외한 단일 진단 비교는
논리 요청 6 → 3, 질문 42 → 9, 모델 보고 input tokens 19,053 → 1,379,
분석 wall time 11.926 → 1.330초였다. 외부 유료 API 비용 측정이나 반복 성능 benchmark는
아니며 다른 정책·공급자에서도 같은 절감률이라고 보장하지 않는다.

### 코드와 모델의 역할 분리로 줄인 분석량

이 변경은 API가 이미 제공한 정형값을 모델에게 다시 해석시키는 작업과,
추출 후 전문을 재전송하는 분류·자체 평가 작업을 제거한다. 코드가 정형값·공식 코드·
명확한 날짜·금액 표현을 먼저 처리하고 모델은 본문의 추가 조건·예외·중복수혜 근거를 찾는다.
모델이 정형값과 상충하는 본문을 선택하면 원본값·수정후보·인용 위치를 함께 남기고,
지원되는 문구는 코드로 재검증한다. 검수된 규칙의 자격 판정과 금액 계산은 코드가 수행한다.
이 분리는 사용자 온라인 요청에 모델 호출을 추가하지 않는다.

위 실제 정책 2건의 동일 로컬 실행 조건을 비교하면 다음과 같다.

| 측정 항목 | 변경 전 | 정형 우선 처리 후 | 비교 |
| --- | ---: | ---: | --- |
| 모델 보고 입력 토큰 | 19,053 | 1,379 | 92.8% 감소, 약 13.8배 적은 토큰 |
| 분석 시간, 모델 로딩 제외 | 11.926초 | 1.330초 | 88.8% 감소, 약 9.0배 빠른 처리 |
| 논리 요청 수 | 6 | 3 | 50% 감소 |
| 모델 질문 수 | 42 | 9 | 78.6% 감소 |

따라서 이 표본에서의 성과는 **입력 토큰 약 13.8배 효율·처리 시간 약 9배 단축**으로
표현한다. 외부 유료 API 청구액을 측정한 결과나 전체 정책의 10배 성능 보장으로 확대하지 않는다.
원본 비교 기록은 Git 제외 경로 `data/local/structured-first-verification/comparison.json`에
있다. 새 버전의 입력·원문 대조와 월세 판정·계산은 각각 별도 검증으로 기록한다.

모든 정형 필드만으로 처리할 수 있는 문서는 모델 호출이 0회다. 미지원 전공·잘못된 코드,
정책별 요청/문자 예산 초과도 호출 전에 분리한다. 본문은 키워드 필터나 일부 잘라내기로
줄이지 않고 보존하며, 공급자 장애를 무제한 분할 재시도로 증폭하지 않는다.

실제 참조는 청년/원가구 소득·재산, 부모 가구 확인 예외, 주거급여 차감, 지역 구직 조건
4개 조항이다. raw Locate의 **원문 전체 연속 문자열 일치 기준**은 2/4였다.
실패한 두 참조도 핵심 내용은 선택했다. 소득·재산은 두 가구의 수치가 각각 추출됐지만
`청년가구`·`원가구` 명칭이 선택 구간에서 빠졌고, 원가구 예외는 앞의 `다만, `만 빠졌다.
따라서 이 결과를 두 조건 자체가 사라졌다고 설명하지 않는다. 가구별 적용 대상을
잃을 위험과 원문 전체 일치 실패는 구분하고, 원래의 엄격한 검증 실패도 그대로 남긴다.
명시적 필드 후보와 문맥을 코드로 보강한 근거·최종 정규화 본문에는 4/4를 보존했다.
새 투영에서도 같은 모델 입력 hash의 실제 GPU 응답 캐시를 검증해 추가 모델 호출 0으로
원문 좌표·최종 근거·정규화 보존을 재확인했다. 모델 원선택과 코드 보강은 별도 기록한다.
이 결과를 모델의 무누락 판정이나 검수 완료·운영 게시로 보고하지 않는다.

이후 월세지원은 원문 보존을 넘어 가구별 소득·재산·원가구 예외·주거급여 월차임분 차감의
실행 규칙과 게시 후 신청자별 계산 경로까지 연결했다. 실제 수집 정책과 가상 신청자를
사용한 29개 경계 사례가 통과했다. 규칙 후보는 미승인 상태로 생성하며, 검수 완료를 가정한
로컬 재생 결과와 운영 DB 게시·배포를 구분한다. 수치·입력 의미·재현 명령은
[월세지원 실행 규칙과 실제 데이터 재생](RENT_SUPPORT.md)을 따른다.

## Snapshot, identity, 비공개 artifact

`scripts/decider_snapshot.py`는 release manifest와 공식 manifest hash를 먼저 확인하고,
각 파일의 크기와 SHA-256을 검사한다. 중단된 `.partial`은 hash가 맞을 때만 재개하며,
완료 파일로 검증되지 않은 byte는 모델로 로드하지 않는다. pinned commit에서 바뀐
README·NOTICE는 코드에 고정한 size/hash로 별도 확인한다.

실행 identity에는 model id, revision, question version, provider, mode,
`fast=false`가 들어간다. response checkpoint는 이 identity와 요청 전체의 canonical
hash로 키를 만든다. 재시작 시 request hash가 다른 cache를 재사용하지 않는다.

비공개 경로는 폴더 `700`, 파일 `600`으로 원자적으로 기록한다.

| 경로 | 내용 |
| --- | --- |
| `data/local/decider/checkpoint` | 검증된 model snapshot |
| `data/processed/decider/responses` | identity·요청 hash별 검증 response cache |
| `data/processed/decider/policies/<key>` | input, status, extraction, normalized, catalog artifact |
| `data/processed/decider/report.json` | 대상·상태·사유·시간 집계 |
| `data/f12-analysis/<task>/<lease>` | 완료 summary와 task output reference |

artifact에는 원문 SHA-256, youthcenter material hash, model identity, 정확한 evidence
offset·score를 남긴다. 로그에는 원문·인용·키를 출력하지 않고 고정 상태와 오류 코드만
사용한다.

## 실행 명령

로컬 의존성은 선택 그룹으로 설치하고 아래 명령도 같은 그룹을 지정한다. 실제 자격 증명은
`data/local/decider.env` 같은 Git 제외 파일에 두며, 아래 경로는 형식만 보여 주는
placeholder다. DSN·API key는 문서·명령 인자·로그에 넣지 않는다.

Triton JIT가 `Python.h`를 찾도록 uv가 관리하는 Python 3.12 header를 지정한다. 경로는
각 개발 PC의 uv 설치 위치에 맞춘 generic path다.

```bash
export C_INCLUDE_PATH="<uv-managed-python-3.12>/include/python3.12${C_INCLUDE_PATH:+:$C_INCLUDE_PATH}"
uv sync --locked --group decider

uv run --locked --group decider python scripts/decider_snapshot.py \
  --output data/local/decider/checkpoint

uv run --locked --group decider python scripts/decider_profile.py \
  --checkpoint data/local/decider/checkpoint \
  --output data/local/decider/profile-synthetic.json \
  --max-length 3072 --batch-tokens 2048
```

snapshot download가 인증을 요구하는 환경에서만 `HF_TOKEN`을 실행 환경에 전달한다.
profile은 합성 입력의 capacity를 재는 도구이며 정책 추출 정확도 평가가 아니다.

읽기 전용 export, manifest 실행, F12 v5 queue, 평가는 다음 명령을 쓴다.

```bash
# F12_WRITE_DATABASE_URL은 data/local/decider.env의 비공개 설정으로 제공한다.
uv run --locked --group decider --env-file data/local/decider.env \
  python -m benefitfinder.jobs decider-batch export \
  --output data/local/decider/export

uv run --locked --group decider --env-file data/local/decider.env \
  python -m benefitfinder.jobs decider-batch run \
  --manifest data/local/decider/export/manifest.json --data-dir data

uv run --locked --group decider --env-file data/local/decider.env \
  python -m benefitfinder.jobs decider-batch run \
  --queue --data-dir data --limit 20

uv run --locked --group decider --env-file data/local/decider.env \
  python -m benefitfinder.jobs decider-batch evaluate \
  --report data/processed/decider/report.json \
  --gold data/local/decider/gold-full.json \
  --output data/local/decider/evaluation.json
```

`export --register`는 같은 input을 F12 v5 queue에 멱등 등록한다. `run --publish`는
`DECIDER_EVALUATION_PATH`와 `POLICY_PUBLISH_DATABASE_URL`을 요구한다. 평가가 통과하지
않았거나 source·lease가 바뀌었으면 게시하지 않는다. `refresh-data`에서 F12 분석을
이어 실행하려면 `F12_ANALYSIS_ENGINE=decider`, `F12_WRITE_DATABASE_URL`을 설정한다.
일일 Decider 분석은 `DECIDER_PROVIDER=external_api`가 필요하며, 실제 공급자·자격 증명을
확인하기 전에는 운영 명령으로 사용하지 않는다.

## 12GB GPU와 memory 동작

현재 로컬 기본값은 `max_length=3072`, `batch_tokens=2048`, `batch_size=4`,
`window_chars=900`이다. FLA `flash-linear-attention==0.5.2` kernel을 사용한다.

- 모든 원문 codepoint는 하나 이상의 window에 들어간다. window는 앞부분을 겹쳐 문맥을
  보존하고, 각 결과의 offset을 다시 원문 좌표로 옮긴다.
- OOM이면 실패 graph를 비운 뒤 batch token을 절반으로 줄이고, 그래도 안 되면 요청을
  나누고, 마지막에 제한 범위 안에서 `max_length`를 줄여 재시도한다. 요청 text를 조용히
  자르지 않는다.
- 끝까지 맞지 않으면 `decider_gpu_memory_exceeded`를 반환한다. 길이 한도는
  `decider_input_length_exceeded`로 분리한다. `held`·`failed`는 완료·게시로 계산하지
  않는다.
- `scripts/decider_profile.py`의 synthetic probe는 capacity 확인용이다. 실제 정책 품질
  결과와 합산하지 않는다.

RTX 3060 12GB에서 확인한 기록은 다음과 같다.

| 실행 | 설정 | 결과 | GPU peak |
| --- | --- | --- | --- |
| v1 실제 한국 공고 10건 | max_length 2048, batch_tokens 2048, batch 4, window 900 | 61.126초, ready 0, held 9, failed 1(`input_length_exceeded`) | allocated 9.792GiB, reserved 10.008GiB, OOM 0 |
| v2 실제 한국 공고 10건 | max_length 3072, batch_tokens 2048, batch 4, window 900 | 55.457초(raw load 제외 batch wall), call 9, ready 0, held 10, failed 0 | allocated 9.7938318GiB, reserved 10.69921875GiB, OOM 0 |

4096 설정은 12GB에 가까운 느린 동작을 보여 작업자가 DB write 전에 직접 중단했다. 완료된
allocator peak 수치가 아니므로 4096 capacity 통과로 기록하지 않는다. v2 10건 결과도
평가 미달이며 대량 완료 실행이 아니다.

## 코드 전용 정규화

정규화는 모델의 자유 서술을 다시 합성하지 않는다. 아래 조건을 모두 코드가 확인해야
catalogue payload를 만든다.

- 공식 문서 1건과 youthcenter source binding 1건, 정확한 policy id·material hash가
  일치해야 한다.
- `scope=youth_employment_or_housing`, `coverage=complete`, `amount_kind=fixed`,
  `eligibility_kind=simple`, `overlap_kind=none_explicit`이어야 한다.
- evidence는 원문의 codepoint 범위·인용문과 정확히 일치하고 유효 score를 가져야 한다. 상한·실비·
  차감·단계·회차·추정 표현이 섞이면 fixed로 승격하지 않는다.
- 고정 월 지급은 금액 하나, 고정 기간 하나, 지원 시작월부터 매월 지급이라는 명시적
  anchor가 모두 있어야 한다. 일시금은 선정 후 즉시·지원 시작월·N개월 뒤 같은 지급
  시점이 하나로 확정돼야 한다. 월 금액과 기간만으로 일정을 만들지 않는다.
- 중복은 `중복 수혜 가능/제한 없음` 또는 `동시 수급 가능`이 원문에 명시돼야 한다.
  제한·차감 문구, 미기재, 불명확은 허용으로 해석하지 않는다.
- 중복 신청·참여 허용이나 특정 정책 쌍 하나의 허용은 모든 정책의 동시 수급 허용으로
  확장하지 않는다. 인용은 잘라 낸 일부 구절이 아니라 원문 한 줄 전체의 문맥으로 다시
  검사한다.
- 생애·횟수 제한은 다른 판단과 독립적으로 누락 여부를 검사한다. `schoolCd`, `jobCd`,
  `zipCd`를 지원 선택값으로 해석하지 못하면 mapping 완료로 추정하지 않고 record를
  보류한다.
- 예외·제외·다만·단서, 지원하지 못하는 자격, 복수 문서·복수 source는 전체 record를
  보류한다. 신청 기간은 유효한 날짜 범위 하나로 확인돼야 한다.
- `consistent >= 0.95`, `readiness >= 1.95`는 **보수적 release gate**이며 사실의 참을
  보증하는 보정 완료 threshold가 아니다.

일부 `cap` 또는 실제 비용 설명에서 숫자 하나를 뽑아 fixed 계산 금액으로 바꾸지 않는다.
정확한 지급 일정과 명시적 중복 허용이 없는 레코드는 `held`로 남긴다.

## Gold와 평가 gate

기본 `strict` 모드의 평가 gold는 `format_version: 1`, `coverage: "full"`, 최소 10개의 고유 policy case를
가져야 한다. 각 case의 `input_sha256`은 실행 report와 같아야 하며, gold 문서의 span은
원문 좌표를 쓴다. 다음은 구조 예시다. 실제 값은 Git 제외 경로에 둔다.

```json
{
  "format_version": 1,
  "coverage": "full",
  "cases": [{
    "policy_id": "youth:youthcenter:<official-id>",
    "input_sha256": "<sha256>",
    "features": ["fixed", "cap", "exceptions", "missing", "contradiction", "overlap"],
    "reviewed_fields": ["amount", "duration", "eligibility", "application", "overlap", "exceptions", "timing"],
    "documents": [{
      "document_id": "<document-id>",
      "spans": [{"kind": "amount", "start": 0, "end": 1}],
      "decisions": {
        "scope": "youth_employment_or_housing",
        "coverage": "complete",
        "amount_kind": "fixed",
        "eligibility_kind": "simple",
        "overlap_kind": "none_explicit"
      }
    }]
  }]
}
```

`strict`에서 각 case의 `reviewed_fields`는 7개 `KINDS` 전체를 포함해야 한다. 해당 kind의 span이 없어도
부재를 사람이 검토했다는 뜻으로 전 종류를 명시한다. 필수 판단 키 5개
(`scope`, `coverage`, `amount_kind`, `eligibility_kind`, `overlap_kind`)는 모든 case에서
평가돼야 한다. 하나라도 빠져 검사 수가 case 수보다 적으면 게시할 수 없다.

평가 산출물은 `criteria_version: "decider-evaluation-v3"`와 `thresholds`에 character
precision 0.95, recall 0.20, 전체 decision accuracy 0.40을 기록한다. 2026-11-16 사용자
승인에 따라 전체 판단 정확도 하한을 0.40으로 변경하고 개별 판단 키 정확도의 차단 기준을
제거했다. 개별 키의 `checks`, `correct`, `accuracy`는 진단 통계로 보존한다.
`require_evaluation`의 기본 `strict` 모드는 버전·임계값 불일치와 점수·필수 조건 미달을 거부한다.

`strict`의 평가 통과 조건은 다음과 같다.

- report의 model id·revision·question version이 실제 실행 client identity와 같고 provider가
  `local` 또는 `external_api`다. mock provider는 통과하지 않는다.
- gold coverage가 `full`이고 모든 case의 `reviewed_fields`가 완전하며, 고유 case 수가
  10 이상이고 여섯 feature(`fixed`, `cap`, `exceptions`, `missing`, `contradiction`,
  `overlap`)가 gold 전체에 있다.
- failed policy가 0이고 character precision 0.95, recall 0.20, 전체 decision accuracy 0.40 이상이다.
- 필수 5개 판단 키는 각 case마다 검사됐고 `require_evaluation`이 같은 조건을 다시 확인한다.

`coverage=critical_evidence_only` 같은 partial gold는 진단용이며 `strict` 통과 근거가 아니다.
정답 누락을 높은 점수로 상쇄하지 않는다.

초기 API 대량 적재는 메인의 source-only bulk → direct pending 경로로 진행하며 모델 추론·
평가를 호출하거나 모델 품질 gate 통과를 기다리지 않는다. 모델 품질 지표는 진단용이다.
원문 일치·금액과 지급 사실 확인·DB 형식 검증은 정책 단위에서 계속 수행한다.

모델 평가를 사용하는 경로에서 `require_evaluation(value, model, mode="mvp")`는 실행 client와
평가의 실제 model id·revision·provider·question identity 일치만 확인한다. mock·다른 identity는
거부한다. 전역 점수 3종, full gold·7필드 참조·5판단 키와 사례 수 완전성, 최소 10건,
feature 충족, 평가 실패 0 조건은 MVP에서 차단하지 않는다. 실패한 정책 자체는 보류한다.
호출부에서 `DECIDER_VALIDATION_MODE`를 읽어 명시적으로 전달하며 기본값은 `strict`다.
`evaluate_report(..., mode="mvp")`는 `mode: "mvp"`, `model_quality_gate: "diagnostic"`,
`relaxed_global_gates`에 완화한 전역 조건을 기록한다. 점수와 `eligible_for_publication`은 계속
strict 기준으로 계산되므로 이 값이 false여도 명시적인 MVP 확인은 통과할 수 있다. 산출물에
기록된 mode만으로 기본 strict 검사가 완화되지는 않는다.

현재 진단은 게시 기준이 아니다. v2 critical reference는 expected 995,
predicted 807, matched 377 characters로 recall 37.889%였다. 여기서 참조 overlap
precision 46.716%는 불완전한 critical reference에만 해당하며 전체 추출 정확도가 아니다.
범주 판단은 29/30(96.667%), amount 9/10, scope 10/10, overlap 10/10이지만 full gold와
character precision 95% 조건을 충족하지 못해 strict의 `eligible_for_publication=false`다.

v1의 critical-only 진단도 보존한다. 10 cases에서 matched 372/expected 995,
recall 0.3738693, 범주 26/30(0.8666667), amount_kind 8/10, scope 9/10,
overlap 9/10이었다. v1 reference precision 0.554396 역시 불완전한 overlap reference에만
해당하며 전체 정확도가 아니다. 두 진단 모두 게시 근거가 아니다.

## 자동 AI release와 중복 방지

이 파이프라인의 자동 게시는 **평가·정규화·source 검사를 통과한 AI release**다. 사람이
대량 검수한 것으로 표시하지 않으며, 기존 사람 검수 경로를 유지한다. 배치는
`writer_agent=decider-locate:<question-version>`, 분리한 후속 검증 단계의
`reviewer_agent=decider-followup-code:<question-version>`, reviewer model의 `id@revision`,
verdict, source/record hash를 `AIReview`에 남긴다. 같은 모델의 별도 요청이며 별도 모델이나
사람이 검수했다고 표시하지 않는다. `reviewed_by`는
사람 이름 대신 `ai:<reviewer>`로 기록한다.

- queue claim은 lease를 갱신하고 게시 직전에 `is_current`를 다시 확인한다. 원문이 바뀌면
  이전 claim의 결과를 게시하지 않는다.
- 동일 input·model·question identity는 같은 artifact를 재사용한다. 동일 request의
  response checkpoint와 동일 release hash는 replay로 처리한다.
- publication은 source generation·policy advisory lock, 현재 released version·hash,
  AI/human review type을 검사한다. 같은 version·hash는 `replayed`이며, 다른 hash는
  conflict다. 기존 release 교체는 별도 expected previous hash와 replacement 승인이
  필요하므로 자동 batch가 사람 release를 덮어쓰지 않는다.
- 같은 온통청년 ID의 `youth:<id>`와 `youth:youthcenter:<id>`를 서로 다른 정책으로
  적재하지 않는다. 기존 표기가 있으면 `ai_catalog_policy_alias_conflict`로 보류하고
  기존 정책 ID를 사용해 입력을 다시 준비한다. 이력과 기존 승인 자료를 보존한다.
- AI 자동 게시에는 0006·0013 migration이 필요하다. 정책 원문 projection·F12 최신
  판단·catalogue source text가 다르면 게시하지 않는다.

## 모델 검수 없는 초기 원천 적재

초기 MVP 적재에는 `decider-batch import-source`를 사용한다. 모델 client를 만들거나
전역 평가 결과를 요구하지 않고 저장된 공식 API 필드로 카탈로그를 준비한다.
`POLICY_PUBLISH_DATABASE_URL`은 비공개 환경 파일에서 읽는다.

```bash
uv run --locked --env-file data/local/decider/production.env \
  python -m benefitfinder.jobs decider-batch import-source \
  --output data/local/decider/source-only-dry-run

uv run --locked --env-file data/local/decider/production.env \
  python -m benefitfinder.jobs decider-batch import-source --publish \
  --output data/local/decider/source-only-production
```

금액과 지급 시점을 확인하지 못한 항목은 `reviewed=false`, `payments=null`로 적재한다.
빈 금액을 0원으로 바꾸지 않는다. 이 상태는 계산용 게시 성공이 아니다.
원문 위치·조건 코드는 수동 확인 조건으로 보존하며, 범위 밖 정책은 원천 DB에 남긴다.
보고서는 전체 원천, 범위 보류, 준비, 신규 미검수 적재, 재실행, 기존 보호, 실패와
계산용 게시를 각각 집계한다.

배치는 10건씩 트랜잭션으로 적재한다. 실패한 묶음은 기존 release를 다시 조회한 뒤
개별 재시도한다. 원자적으로 저장한 비공개 checkpoint와 현재 DB의 해시를 함께 사용해
중단 후 재개하며, 동일 ID의 기존 승인·다른 버전·retired 이력을 자동으로 덮어쓰지 않는다.

고정 지급을 원공고에서 확인한 항목은 별도 순수 코드 컴파일러로 준비한다. 공식 원문
전체 조건·근거와 API/원공고 해시를 연결하고 기존 카탈로그 형식으로 게시한다.
검수 provenance에는 `deterministic-code:no-model-review`를 기록한다. 추가 AI 검수 호출과
사람의 개별 승인 절차는 없다. 원문에 없는 지급일·수혜 횟수·동시 수급 허용은 만들지 않는다.
지원 자격의 미지원 조건은 `needs_review`, 정책 쌍의 미확인 관계는 기존 계산기의 조건부
조합으로 남으며, 실제 수급 확정을 의미하지 않는다.

2026-11-16 source-only 실운영 배치는 원천 4,029건을 분류하고 취업·주거 1,435건을
처리했다. 신규 미검수 적재 443건, 동일 release 30건, 기존 보호 51건, 실패 0건이다.
범위 확인 509건과 컴파일 보류 911건은 성공에 포함하지 않았다. 나머지 2,085건은 MVP
범위 밖 원천이다. 이 배치의 모델·AI 검수 호출은 모두 0회다. 실제 원문과 DB 백업,
checkpoint, 실행 결과는 Git 제외 `data/local/decider/` 아래에 보존한다.

동일 자료 재실행에서는 미검수 release 473건을 재사용했고 추가 적재·실패는 0건이었다.
이 첫 적재 시점에는 정상 release와 원문을 보존한 채, 공식 원공고 기반 계산 정책 2건을
별도로 게시했다. 당시 카탈로그는 현금 572건(계산 가능 2, 미검수 570)과 기존 서비스 15건이며 원공고
원천은 43건이다. 현재 게시된 같은 정책 ID의 중복은 0건이다.

실제 배포 추천 API에 공개 fixture를 바탕으로 한 기장군 25세 구직 조건을 넣어
HTTP 200, `virtual_data=false`, `ai_generated=false`, 1년/PV off 조합 1개와
1,600,000원을 확인했다. 이는 주거지원 1,550,000원과 면접수당 1회 신청분 50,000원이다.
연간 면접수당 상한을 확정액으로 사용하지 않았으며, 일반 자격과 동시 수급 관계는
확인이 필요한 조건부 결과다. 다른 부산 구 조건에서는 지역이 맞는 주거지원만
개별 정책으로 반환됐다. 개별 수혜 이력을 정책 ID로 전달하는 입력은 현재 배포
프로필의 선택 계약에서 지원하지 않으며 원문에 보존한 생애 1회 조건을 확인해야 한다.

실제 PostgreSQL 격리 통합 검사 36건은 원천 변경·멱등 게시·트랜잭션 복구·기존 사람
승인 보호를 확인했다. 가상 모델 응답 테스트와 실제 GPU 실행 기록은 이 결과와
구분한다. 외부 API 연결, 운영 예약 등록, 코드 배포와 main 병합은 수행하지 않았다.

## 모델 없이 계산용 금액 추가 게시

2026-11-16 사용자의 후속 요청에 따라 원천 4,029건 전체를 다시 분류하고 취업·주거
1,944건에 순수 코드 추출을 적용했다. AI 검수, 전역 재현율·판단 정확도 통과 기준,
사람의 개별 승인 없이 원문에 있는 금액·주기와 실행 가능한 코드 조건을 게시한다.
원천 ID·해시·인용 위치, 금액 합계, 날짜 형식, 동일 버전 충돌과 트랜잭션 검사는 유지한다.
계산기는 기존 구현을 사용하며 온라인 모델 호출은 추가하지 않았다.

`source-cash`는 F12 입력·기존 source binding과 F08의 원문 변경 무효화를 재사용한다.
`--as-of`는 모집 상태 계산 날짜이며 감사 기록은 실제 현재 UTC 시각을 사용한다.
기본 실행은 dry-run이고, 사용자가 승인한 운영 초기 적재는 `--publish`로 실행한다.

```bash
uv run --locked --env-file data/local/decider/production.env \
  python -m benefitfinder.jobs source-cash \
  --output data/local/decider/source-cash --as-of 2026-11-16

uv run --locked --env-file data/local/decider/production.env \
  python -m benefitfinder.jobs source-cash --publish --batch-size 10 \
  --output data/local/decider/source-cash --as-of 2026-11-16
```

파일·디렉터리는 비공개로 만들고 항목별 checkpoint·보류 사유를 보존한다. 중단 후에는
현재 DB release를 다시 읽어 동일 해시를 재사용한다. CAS를 적용해 미검수와 이 경로의
버전만 교체하고, 기존 사람 승인·다른 AI release·retired 이력·별칭 충돌은 보호한다.
실패한 트랜잭션만 항목별로 분리해 재시도한다. 보류가 있으면 종료 코드는 1이며,
실패 수와 보류 수를 보고서에서 구분한다. 이 상태를 전체 적재 성공으로 보고하지 않는다.

기존 코드표의 청주·포항·창원 하위 구 전체가 포함되면 현재 지역 목록의 부모 시로
정확하게 매핑한다. 일부 구만 명시된 경우는 시 전체로 넓히지 않는다. 자격 중 현재
선택 계약이 표현하지 못하는 내용은 원문 전체와 함께 `needs_review`로 보존한다.
서로 다른 지급 대상과 프로그램은 합산하지 않는다. 기업 지원과 청년 지원이 함께 있으면
명시된 청년 지원 부분만, 지급 기간이 미확정이면 명시된 한 주기만 계산하고 이를 표시한다.
독립경영 1년차 등 자격의 연차를 지급 기간으로 곱하지 않는다. 실비·월세 상한·대출·
적립식 저축은 고정 현금으로 바꾸지 않는다. 동적 월세 계산은 대응 코드 배포 전에는
기존 운영 reader에 적재하지 않는다.

추가 게시 결과는 계산용 **35건(현재 접수 10, 종료 25)**, 미검수 현금 **543건**,
기존 서비스 15건이다. 이전 미검수 570건에서 27건을 승격하고 보류 목록에서 신규
6건을 게시했다. 기존 2건을 포함한 수치이며 종료 공고를 현재 신청 가능으로 세지 않는다.
새 배치의 게시 실패·정책 ID 중복·모델 호출·AI 검수 호출은 각각 0건이다. 공식 고용24
원문 1건을 추가하면서 기존 원문 43건을 보존했다. 최초 전국 수당 게시 후 집계 SQL의
컬럼 오류가 있었으나 데이터 커밋은 완료됐고, 집계를 수정한 동일 해시 재실행에서 추가
쓰기 없이 복구했다. 실제 원본·before release·소스 표본·배치 결과는 Git 제외
`data/local/decider/mvp-expansion/`에 보존한다.

고용24의 국민취업지원제도 기본 Ⅰ유형 수당은 월 60만원의 기본 6개월분을 계산한다.
가족수당·별도 인센티브·상한 연장기간을 추가하지 않았다. 지급 시작과 개인별 활동계획,
소득 신고, 구직활동 이행은 조건으로 보존한다. 기장의 당해 연도 국취 참여 제한,
청년도전 참여수당과의 동시 참여 제한, 광주 남구의 주 40시간 근로 요건과 국취의
근로시간 제한은 현재 원문 버전의 동시 조합에서 제외했다. 영구·타 연도 금지로 확대하지
않으며 새 원문에서는 관계도 다시 확인해야 한다.

실제 배포 서비스에 가상 대표 조건을 POST한 1년/PV off 응답에서 부산 기장
5,150,000원, 횡성 3,650,000원, 문경 9,600,000원, 구미 3,700,000원,
포항 4,100,000원의 조건부 조합을 확인했다. 부산의 주거지원은 전세피해자 결정 등
추가 자격을 충족해야 한다. 청주에서는 국취 3,600,000원과 도전수당 500,000원을
합산하지 않고 개별 대안으로 표시했다. `virtual_data=false`, `ai_generated=false`이며
수급 확정 결과가 아니다. 최초 병렬 HTTP 검사에서 의존성 조회 503이 4회 있었고,
순차 재확인한 대표 조건은 HTTP 200을 반환했다. 원인과 안정성 문제를 성공 집계로
숨기지 않는다. 광주 남구 코드 12270은 현재 목록의 시도 12에 속하며 울산 코드가 아니다.

원천에 금액이 없는 서비스, 실제 비용·대출 조건이 필요한 정책, 지급 일정 미확인,
분야 밖 하위 분류는 여전히 계산용 보류다. 전체 원천 처리와 계산용 게시 수는 다르다.
외부 API·일일 스케줄은 미등록이며, 코드 배포·운영 스키마 변경·main 병합은 수행하지 않았다.

### 계산용 게시 전 보류 기준

- 월 지급액과 횟수가 있어도 첫 지급월은 추정하지 않는다. 신청·접수 기준 다음 달부터
  지급한다는 근거가 있는 경우만 이 경로의 월별 일정으로 계산한다. 시작월 미확인 또는
  지원하지 않는 일정은 정해진 금액을 읽을 수 있어도 기간 합계와 현재가치의 후보로 게시하지 않는다.
- 공고 전체에서 중복 수급·동시 참여를 제한하는 문구나 적용이 불명확한 규칙을 확인하면
  계산용 게시를 보류한다. 완전히 식별된 정책 쌍의 명시적 무조건 금지만 충돌 규칙으로
  반영한다. 검수에서는 적용 기간과 예외를 함께 확인한다.
- 생애 1회 정책의 개별 수혜 이력은 수혜 범주 선택만으로 확인되지 않는다. 정책 ID를
  확인할 수 없으면 결과에 확인 필요로 남는다.

위 기준은 새로 준비하는 게시 자료에 적용한다. 위 운영 집계는 이전 실행 기록이다.
이미 게시된 지급 일정과 중복 제한은 별도 원문 대조·검수와 게시 이력 정리가 필요하다.

## 2026-11-17 후속 처리

외부 API와 일일 스케줄은 사용자 요청으로 미뤘으며, 후속 적재에서 모델 추론과 AI
검수는 실행하지 않았다. 공식 API 원천은 4,027건이고 취업·주거 관련 대량 진단은
1,944건이다. 공식 링크 223개 중 189개 본문을 보존했고 34개는 요청·변환 실패로
분리했다. HTTP 200 본문에는 메뉴나 정책 목록도 있어 189개를 완전한 공고로 세지 않는다.
면접수당 원공고·FAQ와 경북 애꿈수당의 연도별 안내도 별도로 수집했다. 인용 위치와
본문·원본 해시를 보존했으며 다른 연도의 공고로 일정을 대체하지 않았다.

운영에는 청년월세 특별지원 2차 1건을 미검수에서 계산용으로 전환했다.
기존 `rent_support` 계약과 계산기를 재사용하며 월세 실납부액의 상한을 적용한 뒤
주거급여 월차임분을 차감한다. 월 20만원·최대 24개월이라는 상한을 확정액으로
합산하지 않는다. 모집 종료 상태도 유지했다. 격리 PostgreSQL에서 게시·동일 해시
재실행 후 운영에 반영했으며, 운영 재실행은 추가 쓰기 없이 replay 1건이었다.

첫 반영 시 게시 카탈로그는 593건이었다. 계산용 **36건(접수 10, 종료 26)**,
미검수 현금 **542건**, 서비스 **15건**이며 기존 계산용 35건의 승인 해시는 모두
유지됐다. 이번 추가 운영 게시 1건과 준비만 된 후보를 구분한다. 실제 배포 API의
대표 가상 조건 5개는 HTTP 200이었고 기장 515만원·횡성 365만원·문경 960만원·
구미 370만원의 조건부 조합을 유지했다. 광주 남구는 국취 360만원과 도전수당
50만원을 합산하지 않고 대안으로 반환했다. 수급 확정 또는 동시 수급 허가를 뜻하지 않는다.

### 신청자 사실을 받는 추가 계산 경로

`source_cash_dynamic`은 명확한 1건 정액 또는 실비·전액 보전 근거와 단일 상한만
구조화한다. 실비 금액·지급월을 게시 시점에 만들어 넣지 않는다. `payments=null`과
정책별 `source_cash` 규칙을 저장하고 실제 요청의 선택적
`cash_facts.source_cash`를 기존 계산기에 연결한다. 비용 누락, 지급월 누락, 기수혜·
다른 보전·추가 제외 조건 미확인은 금액을 계산하지 않는다. 다른 정책의 지출액을
재사용하거나 연간 최대 횟수를 곱하지 않으며 원문의 전체 자격·예외는 확인 필요로
보존한다. 이미 명시된 중복 제한을 매핑하지 못한 후보는 계속 보류한다.

이 경로의 최종 후보는 **26건(접수 9, 종료 17)**이다. 실비 19건과 정액 단건 7건이다.
초기 28개 후보 중 사천 면접수당은 공식 모집 종료일이
API 상시 접수와 충돌해 분리했고, 취업·교육 복합 분류 1건도 현재 F12 범위 밖으로
분리했다. 준비 파일 일부가 남아 있어도 최종 manifest의 ID만 사용한다.
실제 정책을 넣은 격리 PostgreSQL에서 최종 26건 게시·26건 replay를 확인했다.
실제 CLI에서도 추가 쓰기·실패·보류 없이 26건을 replay했다. 지역 입력만 기존 공식
목록 스냅숏을 주입했으며 DB·게시 writer·체크포인트는 실제 구현을 사용했다.
로컬 HTTP에서는 청주 응시료 지출 65,000원에 65,000원, 지출 250,000원에 상한
100,000원, 인천 동구 이사비 지출 350,000원에 350,000원을 반환했다. 비용 누락은
금액 후보를 반환하지 않았다. 공통 검사와 가상 원문 CLI 조립·금액·입력 계약 회귀도
실행했다. CLI 조립 단위 검사의 외부 reader는 mock이며 위 PostgreSQL 실행과 구분한다.
이는 운영 배포 검증이 아니며 신청자의 비용과 지급월은 가상 검증값이다.

새 내부 규칙을 기존 운영 reader에 먼저 쓰면 전체 카탈로그 조회가 실패할 수 있다.
따라서 이 코드는 기본 배치에서 활성화하지 않으며, 대응 백엔드 배포와 확인 후에만
다음 옵션으로 준비·게시한다. 운영 스키마 변경은 필요하지 않다.

```sh
uv run --locked python -m benefitfinder.jobs source-cash \
  --applicant-cash-facts --as-of 2026-11-17 \
  --output data/local/decider/applicant-cash
```

`--publish` 없는 실행은 dry-run이다. 실제 게시 전에는 원공고 보충으로 확인한
모집 종료·중복 제한·분류 보류를 반영한 최종 입력을 사용하고, 기존 정상 자료를
보존하는 CAS·배치 트랜잭션·원천 변경 검사를 그대로 적용한다. 컴파일러 감사 버전과
게시 문서 suffix `source-facts-v1`을 분리해 체크포인트·재실행 계약을 유지한다.
실제 원문·준비 파일·before/after release·환경별 보고서는 Git 제외
`data/local/decider/mvp-expansion-20261117/`에 보관한다.

### 승인 후 운영 게시와 배포 API 확인

사용자가 마이그레이션 없는 main 병합·백엔드 배포·기존 계산 확인·추가 적재 순서를
승인했다. [PR #77](https://github.com/x17545/benefitfinder-ai/pull/77)의 병합 커밋
`e263e5b`를 Render가 자동 배포했고 live 상태와 새 선택 입력을 보낸 기존 기장
5,150,000원 조합을 확인했다. 운영 스키마 변경은 수행하지 않았다. 이후 최종
manifest의 26건만 한 트랜잭션으로 게시했다. 미검수 23건을 승격하고 신규 3건을
추가했으며 재실행 26건은 모두 replay였다. 전체 before release와 승인 해시,
체크포인트를 보존했고 비대상 570건의 해시는 바꾸지 않았다.

26건 반영 직후 카탈로그는 **596건 = 계산용 62건(접수 19, 종료 43) + 미검수 519건 +
서비스 15건**이다. 이 후속 작업의 계산용 추가는 월세 1건을 포함한 27건이며,
기존 35건의 승인 해시는 모두 유지됐다. 4,027개 원천은 취업·주거 관련 1,944개와
그 외 2,083개로 나뉜다. 관련 원천은 게시 62개와 보류 1,882개다. 보류에는 금액이
없거나 고정되지 않은 983개, 현행 세부·복합 분류 밖 508개, 비현금·저축 146개,
추가 개인별 지출 구조가 필요한 72개, 미매핑 중복 제한 36개 등이 있다. 기존
미검수 519개 중 509개가 이 원천 목록에 대응하며 나머지 10개는 별도 공식 원공고
자료다. 보류와 게시 수를 합산해 성공으로 세지 않는다.

실제 배포 서비스의 가상 신청자 조건 4개에서 다음을 확인했다.

| 입력 | 해당 정책 계산액 | 기존 정책을 포함한 조건부 조합 |
| --- | ---: | ---: |
| 청주 응시료 지출 65,000원 | 65,000원 | 3,665,000원 |
| 청주 응시료 지출 250,000원 | 상한 100,000원 | 3,700,000원 |
| 인천 동구 이사비 지출 350,000원 | 350,000원 | 3,950,000원 |
| 청주 응시료 지출 미입력 | 해당 금액 미반환 | 해당 정책 제외 |

지급예정월·기수혜·중복 보전·추가 제외 조건은 가상 검증값을 명시했다. 조합에는
자격 확인 필요 조건과 공식 동시 수급 근거가 없는 쌍이 남아 있으며 수급 확정이나
중복 허용을 뜻하지 않는다. 기존 13단계 화면은 이 정책별 사실 입력을 수집하지
않으므로 API 검증과 일반 화면의 입력 범위를 구분한다. 다른 main 변경의 연속
자동 배포와 겹친 최초 확인에서 HTTP 502가 1회 있었고 기록을 보존했다. 안정화 후
4개 조건 모두 HTTP 200으로 재확인했다. 이 작업의 모델 추론과 AI 검수는 0회다.

### 공식 실비 원공고 보충

미게시 실비 정책 79개의 공식 링크를 추가 수집했다. 허용한 직접 링크 56개와 제목이
일치한 페이지의 첨부 4개를 요청해 본문 43개를 확보했고 17개는 요청·변환 실패로
분리했다. 43개에는 캐시 재사용 6개가 포함된다. 지원하지 않는 HWP·압축 파일,
빈 본문과 HTTP 실패를 수집 성공으로 세지 않았다. HTTP 200도 정책 사실 확인과는
구분한다. 기존 수집과 함께 272개 원문·정책 쌍을 코드로 처리해 1개를 준비하고
251개는 보류, 20개는 입력 준비 실패로 기록했다. 이 수는 고유 정책 수와 다르다.

공용 순수 함수 `ingestion.official_expense_cash.compile_official_expense_cash`는 공식
primary와 API 투영을 각각 보존한 `PolicyAnalysisInput`을 받는다. 정책 ID·제목·분류,
신청기간 일치, 공식 주민등록 문구와 API 지역 코드, 단일 실비 상한·지급률을 확인한다.
다른 지급 문구의 상충 상한, 생애 1회, 같은 이름을 쓰는 서로 다른 지역, 미매핑 중복
제한을 처리한다. 전체 자격·예외는 원문 위치와 함께 확인 필요 조건으로 보존하고
`source_cash` 입력과 기존 계산기를 사용한다. 지급월·실제 비용을 생성하지 않는다.
이 초기 배치는 함수를 호출한 뒤 기존 게시 writer에 결과를 전달했으며 새 온라인
계산 경로나 운영 스키마는 추가하지 않았다.

서귀포 응시료 지원은 API에 없던 100,000원 상한을 공식 본문에서 확보했다.
실제 격리 PostgreSQL에서 원문 연결·게시·replay와 비용별 HTTP 계산을 확인한 뒤
운영 공식 원문 세대에 1개를 추가했다. 기존 원문 44개는 변경·누락 0개였고 기존
카탈로그 596개도 보존했다. `PostgresCollectedPolicies.save`의 선택적
`expected_previous_run_id`는 기존 원문 세대에 문서를 합칠 때 사용한다. 기존 source
lock 안에서 읽었던 세대가 그대로인지 검사해 다른 수집이 추가한 원문을 덮어쓰지
않는다. 동일 run의 재실행은 이 검사와 독립적으로 replay된다. 실제 PostgreSQL
9개 통합 검사에서 세대 충돌 시 원문·실행 이력의 무변경과 재실행을 확인했다.

최종 운영 카탈로그는 **597건 = 계산용 63건(접수 20, 종료 43) + 미검수 519건 +
서비스 15건**이다. 원천은 온통청년 4,027개, 공식 본문 45개다. 취업·주거 관련
원천 1,944개는 게시 63개와 보류 1,881개로 나뉜다. 앞선 보류 사유 중 추가 개인별
지출 구조 항목은 72개에서 71개로 줄었고 나머지 사유는 유지됐다. 기존 35개 승인
해시는 보존했으며 이번 후속 추가 게시 합계는 28개다. 보류를 성공으로 세지 않는다.
서귀포도 실제 배포 API에서 지출 65,000원→65,000원·조건부 조합 3,665,000원,
지출 250,000원→상한 100,000원·조건부 조합 3,700,000원, 비용 누락→해당 금액
미반환을 확인했다. 세 조건 모두 HTTP 200이고 모델 추론·AI 검수는 0회다.

## 이전 경로의 2026-11-16 실행 기록

| 항목 | 확인된 상태 |
| --- | --- |
| 공용 코드 | Decider v2, 선택 의존성 그룹, 기본 3072/2048, window 900, batch 4 |
| 모델 평가 | v2 10건 `eligible=false`; 기준 v2에서 precision·amount_kind accuracy 미달 |
| 대량 처리 | 1,944 targets = 509 범위 보류 + 10 검증 보류 + 1,425 미처리. 완료 0 |
| v2 10건 | ready 0, held 10, failed 0, published 0; v1 length failure 이력은 보존 |
| 운영 DB | 실제 운영 정책 쓰기 0 |
| 외부 API | endpoint·자격 증명 미연결, 호스팅 실행 미검증 |
| 스케줄 | 운영 등록 없음 |
| 배포 코드 | 이 Decider 코드는 미배포 |

읽기 전용 export의 target 1,944건 중 1,435건이 selected, 509건이 held였고 2,085건은
공식 분류 또는 하위 범위 밖이었다. held 사유는 취업 하위 분류 범위 확인 458,
혼합 공식 분류 50, 공식 보충 자료·source 준비 1이다. selected 중 10건은 실제 v1·v2
시도로 검증됐지만 모두 게시 불가였고, 나머지 1,425건은 미처리다. 현재 실패는 0이며
과거 v1의 길이 실패 1건은 이력으로 보존한다. 보류·실패·미처리는 완료·게시 수가 아니다.

1,435개 입력 전체의 코드 사전 진단에서도 미해석 자격 코드 필드가 1,435건에 있었고,
현재 컴파일러의 제한 없는 동시 수급 문장·월 지급 시작 기준과 일치하는 입력은 각각
0건이었다. 상한·실비 관련 표현은 445건이었다. 이는 저장된 API 본문의 정규식 진단이며
원공고에도 해당 내용이 없다는 판정이나 모델 검수 완료 수가 아니다. 공식 코드표 매핑,
공고·FAQ 보충, 특정 정책 사이 중복 제한의 실행 규칙 정규화가 필요하다. 현재의 단일
문서·고정 지급·제한 없는 수급만 지원하는 컴파일러로 초기 적재를 완료할 수 없다.

읽기 전용 운영 조회에서는 youthcenter 4,029건과 official 14건을 확인했다. 현금 정책
97건은 모두 `reviewed=false`였고 실제 금액 조합은 0이었다. 승인 record의 존재만으로
계산 준비가 끝나는 것은 아니며, 현재 calculator에 완전한 실행 가능 payload가 없어 빈
결과가 나온다. 원문·비공개 설정은 Git 제외 경로에 보관했고 운영 자료는 변경하지 않았다.

공개 배포의 `GET /api/v1/meta`·`GET /api/v1/options`·`POST /api/v1/recommendations`는
2026-11-16 HTTP 200을 확인했다. `contracts`의 가상 25세·경기·구직·1년·월세 fixture를
넣은 응답은 `virtual_data=false`, `ai_generated=false`, 기본 view가 비어 있었다. 1년·PV
off는 `portfolios=0`, `not_yet_reviewed=55`, `closed_now=36`, `not_eligible=6`이었고
6개 view가 같은 상태였다. HTTP 200은 배포 생존 확인이며 실제 금액 조합·정책 사실 검증이
아니다. Decider도 이 배포에 포함되지 않는다.

Linux systemd 생성기는 `--time HH:MM`을 받고 기본값이 03:00 KST다. 별도 후보는
04:30 KST로 생성해 `systemd-analyze --user verify` 문법 검사를 통과했고 next run은
2026-11-17로 표시됐다. 그러나 `systemctl --user is-enabled dev.benefitfinder.f08.timer`는
`not-found`였으므로 실제 등록·정시 실행은 없다. 외부 분석 공급자를 연결하려면 별도 자격
증명과 배포 검증이 필요하다.

격리 PostgreSQL 최종 전체 검사는 189건 31.366초에 통과했고 lease renew와 정책 ID
별칭 중복 차단을 포함한다. 첫
실행은 동시 접근 검사에서 statement timeout이 발생했으나 같은 전체 검사의 단독 재실행은
통과했다. 병렬 실행 부하의 가능성이 있지만 원인은 단정하지 않는다.
공통 검사와 실제 GPU·PostgreSQL·정책 진단은 별개다. 최종 PR 검사의 명령·결과는 PR에
기록한다. 단위 검사의 가상 응답이나 PostgreSQL의 가상 정책 게시를 실제 정책 게시
실적으로 세지 않는다.

## 외부 serving 후보

[Hugging Face custom handler](https://huggingface.co/docs/inference-endpoints/guides/custom_handler)는
native handler를 올릴 수 있는 template로만 준비했다. 실제 이 모델 지원 endpoint·계정은
확인하거나 구성하지 않았다. [pricing](https://huggingface.co/docs/inference-endpoints/pricing)과
[autoscaling](https://huggingface.co/docs/inference-endpoints/guides/autoscaling)에서
2026-11-16 확인한 조건은 분 단위 과금(초기화 포함), idle 기본 1시간 뒤 503/콜드 스타트,
min 0·max 1 예시다. 공급자별 concurrency·timeout 한도는 아직 확인하지 않았다.

AWS 후보로 관찰한 값은 L4 24GB 시간당 $0.80, A10G 24GB 시간당 $1.00이다. 이는 같은 날
관찰한 가격이며 미래 가격·가용성을 보장하지 않는다. 현재 handler는
`max_length=4096`, 요청당 최대 8개를 선언하지만 플랫폼 한도가 이를 허용하는지는
확인되지 않았다. 로컬 client는 capability probe에서 native model identity,
Unicode codepoint offset, `fast=false`, 네 task와 양의 `max_requests` 한도를 요구하고
필요하면 요청을 나눈다.

`DECIDER_DAILY_CALLS`는 probe·재시도·응답이 모호한 timeout을 모두 포함하는 호출자
예산이다. compute 시간이나 USD 지출 상한이 아니다. native Decider와 동등한 범용
chat/completions API는 확인하지 못했다. Perplexity Sonar나 OpenRouter가 이 native
protocol을 지원한다고 가정하지 않는다.


### 월세지원의 실행 규칙 연결

2차 청년월세의 명시적 가구별 소득·재산·원가구 예외·주거급여 차감은
원문 보존 외에 실행 규칙 후보와 미승인 카탈로그 레코드로 정규화한다.
API 기본값의 수정은 전후 값·원문 위치·모델 근거·검수 상태를 보존한다.
검수 후 게시된 동적 규칙은 사용자별 지급액으로 기존 추천 계산에 연결된다.
범위·입력·수정 승인 경계·실제 정책 경계검증 재현은 [월세지원 실행 안내](RENT_SUPPORT.md)를 따른다.
