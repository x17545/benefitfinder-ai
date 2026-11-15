# 문서 지도

문서를 찾는 첫 진입점이다. 같은 결정을 여러 파일에 복제하지 않고, 아래 표에서 정한 기준 문서를 갱신한다. 링크된 문서의 버전은 본문에서 관리하며 현행 파일명에는 버전 번호를 붙이지 않는다.

## 작업별 읽는 순서

| 하려는 일 | 이 순서로 열어 보기 |
| --- | --- |
| 설치하고 첫 PR 올리기 | [프로젝트 소개](../README.md) → [설치](development/GETTING_STARTED.md) → [협업](../CONTRIBUTING.md) → [Git·PR 절차](development/GIT_WORKFLOW.md) |
| 기능 구현·리뷰 | [작업 지침](../AGENTS.md) → [기능·수락 조건](product/REQUIREMENTS.md#5-기능-상세-요구사항) → [설계](architecture/ARCHITECTURE.md)·[계약](../contracts/README.md) → 모듈 README·하위 지침 |
| 정책 수집·AI 분석 | [데이터 준비](architecture/POLICY_DATA_PIPELINE.md) → [수집·정규화](../backend/src/benefitfinder/ingestion/README.md)·[AI](../backend/src/benefitfinder/ai/README.md) → [실행 명령](../backend/src/benefitfinder/jobs/README.md)·[로컬 보관](../data/README.md) |
| 시연·평가 준비 | [평가자 안내](../README.md#평가자-안내) → [기획서](product/PROJECT_PLAN.md) → [기능별 확인 방법](product/REQUIREMENTS.md#14-구현-현황-표)·[사용자 테스트](product/USER_TEST.md) |

리뷰 시 [검사 범위](development/REPOSITORY_SETTINGS.md#3-현재-공통-검사의-범위)를, 구현 시 [코딩 컨벤션](development/CONVENTIONS.md)을 함께 확인한다. 문서만 편집할 때는 관련 기준과 수정 경로의 지침부터 읽는다.

## 문서별 기준

| 주제 | 기준 문서 | 경계 |
|---|---|---|
| 제출용 기획·목표 기능·평가 대응·일정 | [기획서](product/PROJECT_PLAN.md) | 목표 계획이며 구현·운영·평가 통과 기록과 구분 |
| 문제 배경·초기 계획과 결정 맥락 | [기획 개요](product/OVERVIEW.md) | v3.0 제출 기준은 기획서·기능 명세, 초기 일정은 과거 계획 |
| 기능·화면·문구·계산·수락 조건·역할·회의 항목 | [기능 명세](product/REQUIREMENTS.md) | 6장·11.2는 회의 확인이 필요한 초안 |
| 프레임워크·모듈 경계·확장·기술 결정 | [아키텍처](architecture/ARCHITECTURE.md) | 9장이 기술 결정의 기준 |
| 원문 수집→Agent 추출→검수→게시 | [정책 데이터 준비](architecture/POLICY_DATA_PIPELINE.md) | 사용자 요청의 코드 계산과 구분 |
| API·데이터 교환 형식 | [공용 계약](../contracts/README.md), [JSON Schema](../contracts/v1/) | 현행 개발 계약. 기획자 최종 승인과 구분 |
| 설치·실행·의존성 관리 | [처음 개발 시작하기](development/GETTING_STARTED.md) | 정확한 도구 버전은 선언 파일·lockfile 기준 |
| 작업 분담·개발·검증·AI 표시·리뷰 | [CONTRIBUTING.md](../CONTRIBUTING.md) | 기획 수정과 인계 절차 포함 |
| 이름·코드·기록·개인정보 처리 규칙 | [코딩 컨벤션](development/CONVENTIONS.md) | 설계·Git 명령은 해당 기준 문서 참조 |
| 브랜치·main 동기화·충돌·커밋·푸시·PR | [Git 작업 절차](development/GIT_WORKFLOW.md) | 허용 접두어, PR 전 main 병합 필수 |
| 검사 범위·에이전트 연결·GitHub 설정 | [저장소 운영 안내](development/REPOSITORY_SETTINGS.md) | 원격 보호 설정은 현재 미적용 |
| 사용자 관찰·설문·결과 기록 | [사용자 테스트](product/USER_TEST.md) | 성공 기준은 기능 명세 8장 참조 |
| 작업 전 필수 행동 | [AGENTS.md](../AGENTS.md)와 수정 경로의 하위 지침 | 위 상세 기준으로 연결하는 공용 진입점 |

명시적인 사용자 요청과 합의된 회의 결정을 먼저 반영한다. 같은 주제에 설명이 다르면 위 기준 문서를 확인하고 관련 문서를 함께 고친다. 서로 다른 주제의 충돌이나 미확정 항목은 담당자에게 확인한다. 파일 날짜가 최신이라는 이유만으로 기능·계약·기술 결정을 덮어쓰지 않는다.

## 구현 상태

2026-11-12, 현재 코드의 구현 기준이다. 배포 완료를 뜻하지 않는다.

> **확인 범위:** 기본 개발 환경은 가상 정책으로 동작한다. 게시 정책 DB와 로컬 모델은 별도 설정으로 검증했다. 실제 정책 게시, 운영 배포, 평가 결과는 각 환경에서 따로 확인한다.

| 먼저 확인할 것 | 기본 개발 환경 | 설정해 확인할 것 |
| --- | --- | --- |
| 추천·상황 변화·상세 | 공식 지역 + 가상 취업·주거 정책 | `POLICY_DATABASE_URL`: 검수·게시된 현금 정책 |
| F13 설명 | 기본 문구 | `DATABASE_URL`: 게시 원문 조항 조회. 모델을 설정하면 선택적으로 설명 생성 |
| 분석 횟수 | 단일 프로세스 메모리 | `ANALYSIS_USAGE_DATABASE_URL`: PostgreSQL 공유 집계 |

### 화면과 정책 계산

- **사용 흐름:** S1에서 시작해 13단계 조건을 고르면 분석 진행·오류를 거쳐 S3 조합과 보완 목록을 확인한다. S4는 네 가지 상황 변화를 다시 계산하고, S5는 상세·진단을, S6는 가상 서비스를 보여 준다. 화면은 라우팅·중첩 응답 검증을 적용하고, 개발용 404 예시와 서버 가상 결과를 구분한다. 서버 가상 결과는 화면과 저장 이미지에 명시한다.
- **추천·상세:** [API 안내](../backend/src/benefitfinder/api/README.md)에 추천·상황 변화·상세 경로, 공통 오류·OpenAPI가 있다. 게시 현금 정책은 후보·자격·기간별 조합·상세·상황 변화를 계산하며, 정확한 그래프 탐색·동률 정렬·자원 한도·보완 목록을 지원한다. F14의 서비스 환산액은 근거가 있을 때만 현금 조합과 따로 표시한다.
- **공식 지역:** [`GET /api/v1/options`](../backend/README.md#실제-지역-선택-api)는 현행 법정동코드에서 자치 시군구를 제공한다. 조회 실패 시 가상 지역으로 대체하지 않고 빈 목록·실패·재시도를 표시한다. 분석 실패 후 예제 결과 버튼은 개발 모드에서만 보인다.
- **기억하기·삭제:** 탭 안 입력·결과·기억하기 선택을 복원하고 조건 변경이나 캐시 손상 시 결과를 지운다. 선택한 조건은 분석 시작 시 브라우저에 저장하고 30일 후 다음 접속에서 삭제한다. 전체 삭제와 조합 PNG 저장을 제공한다. P1·P2와 조건 입력 8·9·12단계에 서버 무저장, 탭 임시 보관, AI 설명의 전달 범위와 GA 미연결을 안내한다.
- **후속:** 실제 정책 30개 검수·게시, ECOS 금리·만기 적립, 서비스·단가 자료의 DB 연결.

### 수집·검수·원문 근거

- **[F08 자동 갱신](../backend/src/benefitfinder/jobs/README.md#f08-온통청년-갱신-한-번-실행):** 온통청년 목록·원본·버전을 비교하고 전체 건수·고유 ID를 검사해 JSON·CSV로 저장한다. 일시 실패는 한 번 재시도하며 전체 성공 후에만 최신본을 바꾼다. 중복 실행 차단, 디스코드 알림, macOS 03:00 KST `launchd` 설정 생성을 지원한다.
- **[F12 추출·검수](../backend/src/benefitfinder/jobs/README.md#승인-항목만-로컬-파일로-내보내기):** 공식 공고 한 건에서 중복 제한과 자격·금액·지급 시점 후보를 두 검수 대기 파일로 만든다. 항목별 승인·반려·사유를 기록하고 사람이 승인한 항목만 원문 근거와 함께 내보낸다.
- **[F12 긴 문서 추출](../backend/src/benefitfinder/jobs/README.md#긴-공식-지침의-문맥-분할-추출) · [품질 평가](../backend/src/benefitfinder/jobs/README.md#f12-추출-품질-평가):** 문서를 페이지·문단으로 나눠 앞뒤 문맥과 함께 로컬 E4B로 근거를 고른다. 로컬 검수 자료·승인 내보내기와 추출 결과를 비교해 누락과 원문의 차이를 확인한다.
- **[F13 로컬 설명 코어](#f13-로컬-설명-코어):** 추천 API·결과 화면에 게시 조항과 원문 위치·확인일을 연결했다. PostgreSQL 또는 가상 메모리에서 조항을 조회한다. DB 중단에는 기본 안내를 표시하고 계산을 보존한다.

<details>
<summary>검수·평가의 보존 기준과 후속 작업</summary>

- **F08:** 최종 실패 시 이전 성공본·시각을 유지한다. 상태 교체에 실패하면 이번 실행의 미채택 생성본만 정리하며 선택 여부가 불명확하면 보존한다. 실제 API 수집·알림 도착·로컬 스케줄 등록과 PostgreSQL 적재·공고 원문 수집 연결은 후속이다.
- **F12 검수:** 이어진 자격 조건·소득/휴폐업 예외, 실제 비용·생애 한도·기수혜/타 급여 차감 FAQ, 여러 줄 인용·별칭·예외를 보존한다. 모호한 수치·지급일은 추정하지 않고 원문·후보 변경 시 이전 판단을 재사용하지 않는다. 별도 보충 지침의 URL·버전·해시와 근거도 독립적으로 검수한다. 여러 문서 일괄 추출, PostgreSQL 검수 저장, 추출 결과의 운영 게시·계산 연동은 후속이다.
- **F12 평가:** FAQ·별표 각주, 원문 위치·버전·예외를 보존하고 모델 원선택과 보강 문맥을 구분한다. v1 제한을 유지한 v2 대량 입력과 자원 한도 안에서 인용 누락·예외 필드·위치·버전·해시·수치 차이를 평가한다. 추출 실패·다른 버전은 평가 불가, 전체 점수는 미확인으로 남긴다. 모델·DB·승인·게시 상태는 바꾸지 않는다.
- **F13:** 실제 정책 자료 게시와 정책 사실 평가는 후속이다.

</details>

### PostgreSQL과 AI 검증

- **조항 저장소:** 문서·조항·게시/검수 상태를 명시적 SQL로 마이그레이션한다. 비동기 조회에서 상태·버전을 구분하고, 같은 정책의 별도 지침은 URL·버전·해시를 분리한다. 미게시 승인 근거는 기존 행을 보존해 추가하고 지정 버전·항목만 화면에서 확인한다. 별도 PostgreSQL 17에서 읽기·쓰기·장애·화면을 검증했다.
- **현금 정책 카탈로그:** 0002 마이그레이션·승인 해시 검증·명시적 게시 CLI·읽기 전용 조회를 제공한다. PostgreSQL 17에서 가상 자료의 게시·버전 교체·롤백·권한과 추천·상세 연결을 검증했다.
- **실제 승인 근거:** 별도 로컬 `_test` DB에 승인 내보내기만 `draft`로 적재한다. 원본 ID·해시·검수자·사유·범위를 보존한다. #19를 결합한 검증 서버에서 실제 지역 입력부터 E4B 안내·원문 표시까지 확인했다. 자격 확인 전·금액 `null`·조합 없음 상태를 유지한다.
- **모델 실행:** 개발용 [llama.cpp](development/LOCAL_LLM.md)는 로컬 GPU에서 F12 도구 호출·문맥별 추출과 F13 설명을 같은 설정으로 연결한다. 운영은 외부 API의 HTTPS 주소·모델·키를 명시한다. 기존 Ollama는 명시적 호환 경로다. 외부 공급자 평가는 별도다.

### 서비스 운영과 배포

| 항목 | 확인 범위 |
| --- | --- |
| [서비스 메타데이터](../backend/src/benefitfinder/api/README.md#서비스-메타데이터) | `GET /api/v1/meta`: 최근 게시 자료 시각·추천과 같은 할인율 상태·공유 잔여 횟수를 조회한다. 조회는 차감하지 않는다. 가상 자료는 `virtual_data: true`, 게시 자료가 없으면 503이다. 실제 ECOS 수집·갱신과 화면 연결은 후속 |
| [공유 분석 한도](../backend/src/benefitfinder/infrastructure/database/ANALYSIS_USAGE.md) | 추천·상황 변화 합계 하루 100회. PostgreSQL 설정 시 KST 날짜별 익명 집계로 공유하고 DB 장애 시 503이다. 미설정 시 단일 프로세스 메모리를 쓴다 |
| [분석 진행과 취소](../backend/src/benefitfinder/api/README.md) | 추천은 JSON 또는 POST SSE로 결과를 제공한다. SSE는 실제 계산·서비스·설명 진행을 보내고 결과 또는 오류로 한 번 종료한다. 추천·상황 변화는 최대 60초이며, 취소·오류 뒤 성공 방지는 ASGI 수준에서 검증했다 |
| 배포·통계·검사 | Vercel 환경별 API 프록시·SPA 경로와 Render 실행 설정이 있다. 미연결 Preview의 API·health는 503을 반환한다. 실제 배포·환경 변수 연결·접속 검증은 별도이며 GA는 미연결이다. uv·pnpm, 잠금 파일, 계약 타입 생성, 공통 검사·GitHub CI를 제공한다 |

`/health`는 프로세스 생존 확인이다. 목표 아키텍처, 디렉터리 존재, 참조 계산 테스트 통과만으로 기능·운영 검증을 판단할 수 없다. 실제 동작과 검사 범위는 코드·테스트·[검사 안내](development/REPOSITORY_SETTINGS.md#3-현재-공통-검사의-범위)를 확인한다. 구현 상태가 바뀌면 이 절을 갱신한다.

## 화면 시안과 과거 기록

- [클릭 시안](prototype/index.html)은 화면 참고용이다. PNG·HTML의 과거 수치보다 현행 기능 명세가 우선하며 제품 앱은 `apps/web/`에서 구현한다.
- [과거 기록 목록](archive/README.md)은 이전 기획·ID 대응표·계산 예시를 보존한다. 에이전트의 기본 읽기 대상이나 현행 수락 기준으로 사용하지 않는다.
- 실제 경로·책임은 [웹 README](../apps/web/README.md)와 [백엔드 README](../backend/README.md)를 따른다. 모듈 README에는 해당 모듈의 책임과 인계만 적고 공통 규칙을 복제하지 않는다.

## 이번 위치 변경

| 이전 | 현재 |
|---|---|
| `REQUIREMENTS_v2.md` | `docs/product/REQUIREMENTS.md` (내용·기능 ID·장 번호 유지) |
| `docs/PROJECT_PLAN.md` | `docs/product/OVERVIEW.md`로 배경·일정·제출물 정리, 전체 이전 본문은 archive에 보존 |
| `docs/REQUIREMENTS.md` | 이 문서로 안내 통합, 이전 본문·구 ID 대응표는 archive에 보존 |
| `docs/CONTRIBUTING.md` + `docs/DEVELOPMENT.md` | 루트 `CONTRIBUTING.md`로 통합 |
| `docs/ARCHITECTURE.md` + `docs/POLICY_DATA_PIPELINE.md` | `docs/architecture/` |
| 설치·컨벤션·Git·저장소 설정 문서 | `docs/development/` |
| `docs/USER_TEST.md` | `docs/product/USER_TEST.md` |
| `docs/*_ARCHIVE.md` | `docs/archive/` |

루트 README → 이 문서 → 주제별 기준 → 모듈 README·계약 순서로 연결한다. `CLAUDE.md`와 Cursor 규칙은 계속 루트 `AGENTS.md`만 연결한다. 이전 위치에 같은 내용의 사본이나 별도 버전 파일을 만들지 않는다.

## F13 로컬 설명 코어

[로컬 설명 명령](../backend/src/benefitfinder/ai/README.md#f13-맞춤-설명-로컬-검증), [추천 API 연결](../backend/src/benefitfinder/api/README.md#f13-설명-실행), [결과 화면의 설명·원문 근거](../apps/web/README.md#f13-결과-설명과-원문-근거)를 제공한다. 정책·버전별 게시 조항 검색, 일반 판정·코드 계산값만 전달, 인용 검사, 검색을 포함한 5초 제한과 기본 문구 전환을 구현했다. `DATABASE_URL` 설정 시 [PostgreSQL 게시 조항](../backend/src/benefitfinder/infrastructure/database/README.md)을 조회한다. 검증은 실제 PostgreSQL 17·가상 취업/주거 조항·로컬 E4B 범위이며 실제 정책 자료의 게시·신청 사실 검증 완료를 뜻하지 않는다. 기본 공통 검사와 [별도 PostgreSQL 검사](../backend/integration/README.md)를 구분한다.
