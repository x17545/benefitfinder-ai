# Decider 정책 추출·게시 파이프라인

2026-11-16 현재 코드와 실행 기록을 정리한다. 이 문서는 구현·검증 상태를 설명하며
[기능 명세](../product/REQUIREMENTS.md)의 수락 조건이나 사람 검수 목표를 바꾸지 않는다.
Decider는 데이터 준비 단계의 오프라인 추출기다. 사용자 추천 요청에서 호출하거나
LLM이 자격·금액·조합 순위를 덮어쓰는 경로가 아니다.

## 현재 구현과 경계

- [pplx-decider-flash-v1.3](https://huggingface.co/perplexity-ai/pplx-decider-flash-v1.3)은
  Apache-2.0 공개 모델이다. 현재 코드는 revision
  `d06fb3f2d97e0a52325a0d463c450cf0338f44fe`의 manifest·파일 크기·SHA-256을 확인한
  snapshot만 로드한다. 내려받은 코드를 검증 전에 실행하지 않는다.
- 로컬·호스팅 공통 경로는 native Decider의 `locate`와 후속 `choice`·`noul`·`score`를
  분리한다. 로컬 모델은 `fast=False`, `locate=True`, `mode="separate"`로 고정한다.
  문서별 locate 결과를 만든 뒤 그 결과와 전문을 후속 판단 요청에 넣는다.
- `youth-decider-v2`가 현재 공용 질문 버전이다. 한글·영문 병기 locate 질문과 후속
  metadata 중복 제거를 반영했다. 이전 `v1` 측정 기록은 아래에 별도로 보존한다.
- F12의 기본 엔진은 기존 `tools`다. `F12_ANALYSIS_ENGINE=decider`를 명시할 때
  analysis version 5와 Decider 경로를 쓴다.
- Decider 의존성은 선택 그룹 `decider`에 있다. 기본 API·웹 설치를 무겁게 만들지 않으며,
  로컬 실행은 `uv run --locked --group decider ...`로 격리한다.
- 현재 코드는 배포되지 않았다. 운영 DB 쓰기, 외부 API 연결, 운영 스케줄 등록도 없다.
  초기 단계는 사용자가 명시한 로컬 실행 범위다.

## 처리 흐름

1. 공식 온통청년 취업·주거 원문만 읽기 전용으로 내보낸다. 공식 분류가 혼합됐거나
   취업 하위 분류가 확인되지 않으면 추론하지 않고 보류한다.
2. 문서를 codepoint 손실 없이 겹치는 window로 나눈다. 각 window에서 7종
   `locate` 질문을 실행한다. window 경계는 원문 좌표로 다시 결합하고 인용문이 원문과
   정확히 일치하는지 확인한다.
3. 전문과 unique `(kind, text)` evidence로 dependency 있는 판단을 한 번 더 요청한다.
   v2의 후속 요청은 반복 offset·score metadata를 줄이지만 전문과 모든 unique
   evidence text는 보존한다. 정확한 offset·score는 추출 artifact에 원본대로 남긴다.
4. 코드가 scope·coverage·amount kind·eligibility·overlap·정확한 지급 일정·신청 기간을
   검증하고 catalogue 형태로 정규화한다. 해석이 필요한 항목은 게시 대상에서 보류한다.
5. 평가 gate와 source freshness를 통과한 항목만 명시적인 AI release 경로로 게시한다.

`LOCATE_QUESTIONS`의 7종은 `amount`, `duration`, `eligibility`, `application`,
`overlap`, `exceptions`, `timing`이다. 후속 판단은 `scope`, `coverage`,
`amount_kind`, `eligibility_kind`, `overlap_kind`, `consistent`, `readiness`를
구분해 저장한다.

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

평가 gold는 `format_version: 1`, `coverage: "full"`, 최소 10개의 고유 policy case를
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

각 case의 `reviewed_fields`는 7개 `KINDS` 전체를 포함해야 한다. 해당 kind의 span이 없어도
부재를 사람이 검토했다는 뜻으로 전 종류를 명시한다. 필수 판단 키 5개
(`scope`, `coverage`, `amount_kind`, `eligibility_kind`, `overlap_kind`)는 모든 case에서
평가돼야 한다. 하나라도 빠져 검사 수가 case 수보다 적으면 게시할 수 없다.

게시 가능 조건은 다음과 같다.

- report의 model id·revision·question version이 실제 실행 client identity와 같고 provider가
  `local` 또는 `external_api`다. mock provider는 통과하지 않는다.
- gold coverage가 `full`이고 모든 case의 `reviewed_fields`가 완전하며, 고유 case 수가
  10 이상이고 여섯 feature(`fixed`, `cap`, `exceptions`, `missing`, `contradiction`,
  `overlap`)가 gold 전체에 있다.
- failed policy가 0이고 character precision·recall·decision accuracy와 각 판단 키
  accuracy가 모두 0.95 이상이다.
- 필수 5개 판단 키는 각 case마다 검사됐고 `require_evaluation`이 같은 조건을 다시 확인한다.

`coverage=critical_evidence_only` 같은 partial gold는 diagnostic만 가능하다. 완전한
정답으로도, 자동 게시 승인으로도 사용할 수 없다. 정답 누락을 높은 점수로 상쇄하지 않는다.

현재 진단은 게시 기준이 아니다. v2 critical reference는 expected 995,
predicted 807, matched 377 characters로 recall 37.889%였다. 여기서 참조 overlap
precision 46.716%는 불완전한 critical reference에만 해당하며 전체 추출 정확도가 아니다.
범주 판단은 29/30(96.667%), amount 9/10, scope 10/10, overlap 10/10이지만 필수
recall·precision 95%를 통과하지 못해 `eligible_for_publication=false`다.

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

## 2026-11-16 현재 상태

| 항목 | 확인된 상태 |
| --- | --- |
| 공용 코드 | Decider v2, 선택 의존성 그룹, 기본 3072/2048, window 900, batch 4 |
| 모델 평가 | v2 10건 `eligible=false`; 95% gate 미달 |
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
