# AI 혜택 코치 (BenefitFinder AI)

**내 조건에서 함께 받을 수 있는 청년 지원금을 묶고, 조합별 총액과 선택 이유를 비교하는 서비스**를 만든다. 화면 이름은 ‘청년 정책 매니저’이며, 첫 MVP는 만 19~39세 청년의 **취업·주거 혜택**에 집중한다.

> 코디세이 AI 네이티브 Final Project · 5인 팀

[서비스 체험](https://benefitfinder-ai.vercel.app/) · [시연 영상](#시연-영상) · [프로젝트 흐름](#프로젝트-흐름-한눈에-보기) · [프로젝트 구조 지도](https://x17545.github.io/benefitfinder-ai/) · [로컬 실행](#로컬-실행) · [팀 역할](#팀원-역할과-기여) · [결과](#결과) · [평가자 안내](#평가자-안내) · [문서 지도](docs/README.md)

## 프로젝트 흐름 한눈에 보기

```mermaid
flowchart TB
    accTitle: 정책 준비에서 사용자 결과까지
    accDescr: 공식 자료의 정형값을 코드로 처리하고 본문의 추가 조건은 AI로 추출한다. 근거와 실행 규칙을 검증해 게시하며 미확인 내용은 검수 대상으로 남긴다. 사용자 조건과 게시 자료를 코드가 판정하고 계산한다.
    Source["공식 API·정책 원문"] --> Prepare["정형값 코드 처리 · 본문 AI 분석(선택)"]
    Prepare --> Review["근거·금액·지급 규칙 검증"]
    Review -->|"실행 규칙 확인"| Catalog[("정책·규칙·근거")]
    Review -->|"미확인 내용"| Pending["검수 대기 · 확인 필요"]
    Catalog --> Compute["코드 · 자격 판정과 조합 계산"]
    Profile["사용자 · 조건 13단계 선택"] --> Compute
    Compute --> Result["조합 비교 · 상황 변화 · 상세"]

    classDef data fill:#F2E9FF,stroke:#7251A3,color:#3E2869;
    classDef published fill:#DDF7ED,stroke:#137A60,color:#073F33;
    classDef app fill:#E8F1FF,stroke:#3863A3,color:#152F56;
    class Prepare,Review,Pending data;
    class Catalog published;
    class Compute,Result app;
```

- **정책 준비:** API의 정형 필드는 코드로 처리하고 본문의 추가 조건·예외·중복수혜는 AI 분석으로 선택적으로 보완한다. 원문·해시·금액·지급 규칙을 검증하며, 실행하지 못하는 조건은 확인 필요로 남긴다. 코드 기반 원천 적재와 검수·게시 경로는 [데이터 준비 흐름](docs/architecture/POLICY_DATA_PIPELINE.md), [실행·운영 기록](docs/development/DECIDER_PIPELINE.md)을 따른다.
- **사용자 요청:** 코드는 13단계 선택값과 정책을 비교해 자격·중복 제한·총액을 계산한다. 사용자는 조합과 상황 변화를 비교하고, 상세에서 근거와 공식 신청 안내를 확인한다. AI 설명은 선택 사항이다. [화면 흐름](docs/product/REQUIREMENTS.md#41-화면-목록과-흐름)

**지금 체험할 수 있는 범위:** [배포 서비스](https://benefitfinder-ai.vercel.app/)는 실제 게시 정책을 기반으로 조합·상황 변화·상세를 제공한다. 미확인 자격과 동시 수급 관계는 확인 필요로 표시한다. 기본 로컬 실행은 공식 지역 목록과 가상 정책을 사용하며, 가상 결과에는 표시가 붙는다. 확인한 운영 결과와 로컬 검증은 [현재 개발 상태](#현재-개발-상태)와 [결과](#결과)에서 구분한다.

단계별 문서는 GitHub에서 바로 읽을 수 있는 [문서 지도](docs/README.md)에서 찾을 수 있다.

## 해결하려는 문제

취업을 준비하거나 주거비를 줄이려는 청년은 여러 지원정책을 찾더라도 **그중 무엇을 함께 받아야 가장 이득인지** 다시 판단해야 한다. 정책마다 나이·소득·거주·가구 조건이 다르고, 다른 지원금과 함께 받을 수 없는 조건이나 예외도 있기 때문이다.

| 사용자가 막히는 질문 | 직접 비교해야 하는 내용 |
|---|---|
| 내 조건으로 받을 수 있나? | 공고별 자격 조건과 내 정보, 아직 확인하지 못한 조건 |
| 이 지원금들을 같이 받아도 되나? | 정책 사이의 중복 수급 제한·기간·예외 |
| 어떤 조합이 더 이득인가? | 함께 받을 수 있는 조합별 총액, 받는 기간과 시점 |
| 취업하거나 소득이 오르면 무엇이 달라지나? | 끊기거나 새로 받을 수 있는 지원금과 총액 변화 |

이 비교를 혼자 하다 보면 함께 받을 수 없는 지원금을 합산해 기대하거나, 개별 금액만 보고 더 유리한 조합을 놓칠 수 있다. 이 프로젝트는 **혜택을 찾은 뒤의 선택과 비교 부담**을 줄이는 데 초점을 둔다.

## 왜 조합을 비교해야 할까?

아래는 원리를 설명하기 위한 **가상 예시**다. 실제 정책·지급액이나 기능 명세의 정답 자료가 아니다. 사용자가 세 지원금의 자격을 모두 충족하고, 같은 1년 동안 받을 금액과 중복 조건이 모두 확인됐다고 가정한다. 여기서는 현재가치 할인을 적용하지 않은 지원액을 비교한다.

| 가상 지원금 | 1년 지원액 | 함께 받을 수 없는 지원금 |
|---|---|---|
| A | 200만 원 | B, C |
| B | 180만 원 | A |
| C | 150만 원 | A |

```mermaid
flowchart LR
    Pick{"1년 지원금 선택"}
    Pick -->|"A 선택"| A["A 단독: 200만 원"]
    Pick -->|"B와 C 선택"| BC["B + C: 330만 원"]
    classDef best fill:#DDF7ED,stroke:#137A60,color:#073F33,stroke-width:2px;
    class BC best;
```

개별 금액은 A가 가장 크지만, B와 C를 함께 받으면 **1년 총액이 130만 원 더 커진다.**

실제 조건 입력과 조합 비교는 [배포 서비스](https://benefitfinder-ai.vercel.app/)에서 체험할 수 있다.

## 목표하는 사용 경험

1. **내 조건 입력:** 주거지·소득·연령 등을 13단계 선택지로 고른다.
2. **자격과 조합 비교:** 조건별 진단과 함께 추천 조합·대안의 총액, 중복 제한 때문에 포기하는 지원금과 근거를 확인한다. 기간과 현재가치를 바꿔 비교할 수 있다.
3. **상황 변화 확인:** 취업·독립·소득 변화에 따라 어떤 지원금과 총액이 달라지는지 비교한다.
4. **신청 준비:** 확인할 조건·공식 공고·신청 방법으로 이어진다.

확인된 금액·조건을 기준으로 비교하고, 불명확한 자격·중복 조건은 ‘확인 필요’로 표시한다. 실제 수급 가능 여부는 신청 기관의 확인을 거친다.

이를 위해 공식 API의 정형 필드를 먼저 처리하고, 충분히 확인되지 않는 제한·예외는 공고·지침·FAQ에서 분석해 검수 대상으로 남긴다. 사용자 요청의 자격 판정·금액·조합 순위는 게시된 규칙과 코드가 계산한다. 상세한 문제 정의는 [기획서](docs/product/PROJECT_PLAN.md#1-문제-정의), 기능·계산 기준은 [기능 명세 v3.0](docs/product/REQUIREMENTS.md)을 따른다.

선택 입력 13단계와 F01~F14를 취업·주거에 적용하며, 생활·자산형성·교육·장학과 다른 대상의 혜택은 확장 단계에서 추가한다.

## 현재 개발 상태

| 영역 | 지금 확인할 수 있는 것 | 별도 검증·후속 |
| --- | --- | --- |
| 웹과 API | 공식 지역 선택 → 13단계 입력 → 조합·상황 변화(S4)·상세(S5)·서비스(S6). 입력·결과 탭 복원, 조건 기억하기·삭제, PNG 저장. 기본 로컬 실행의 가상 결과는 화면과 이미지에 표시 | 실제 사용자 테스트와 환경별 동작 확인 |
| 정책 계산·설명 | 배포 환경은 [게시 현금 정책 카탈로그](backend/src/benefitfinder/infrastructure/database/POLICY_CATALOG.md)로 계산. F13은 [게시 조항과 선택적 모델](backend/src/benefitfinder/api/README.md#f13-설명-실행) 사용. [게시 서비스 카탈로그](backend/src/benefitfinder/infrastructure/database/SERVICE_CATALOG.md)의 환산액은 현금 조합과 분리 | 미확인 자격·중복수혜·지급 조건 검수와 계산 가능 정책 확대 |
| 수집·검수 도구 | [F08 통합 갱신](backend/src/benefitfinder/jobs/DAILY_REFRESH.md), [수집본 DB 인계](backend/src/benefitfinder/infrastructure/database/COLLECTED_POLICIES.md), [F12 분석 큐](backend/src/benefitfinder/infrastructure/database/F12_ANALYSIS.md)와 [입력 자동 준비](backend/src/benefitfinder/infrastructure/database/F12_PREPARATION.md). 정형 우선 분석·모델 없는 원천 적재·코드 기반 계산용 게시 구현 | Render Cron 03:00 KST 실행·Discord 알림 확인 완료, 미검수·보류 자료 처리는 후속 |

F11 이용 통계는 명세의 17개 이벤트를 기존 백엔드를 통해 GA4로 전달하도록 연결했다. 사용자 조건·URL 원문은 제외하고 통계용 쿠키·브라우저 저장소 없이 임시 식별자를 사용한다. 기본 설정은 꺼짐이며, 실제 조장 계정 수신과 팀원 보기 권한은 [통계 설정 안내](backend/src/benefitfinder/infrastructure/ANALYTICS.md)에 따라 별도 확인한다.

실행 모드와 검증 범위는 [문서 지도의 구현 상태](docs/README.md#구현-상태)를 확인한다.

## 평가자 안내

아래 주소에서 배포된 앱을 확인할 수 있다. 목표 기능과 현재 구현·운영 검증은 [현재 개발 상태](#현재-개발-상태)와 [문서 지도](docs/README.md#구현-상태)에서 구분한다. 클릭 시안은 실제 앱의 기능 검증과 구분한다.

| 항목 | 내용 |
| --- | --- |
| 배포 URL | [**benefitfinder-ai.vercel.app**](https://benefitfinder-ai.vercel.app/) |
| 시연 영상 | [1분 모바일 데모 재생](#시연-영상) |
| 접속 방법 | 로그인·회원가입 없이 위 주소로 바로 접속한다. 평가 전용 계정은 필요 없다 |
| 서비스 유지 | 결과 안내를 받을 때까지 유지한다 |
| 기획서 | [docs/product/PROJECT_PLAN.md](docs/product/PROJECT_PLAN.md) |
| 기능 요구 명세서 | [docs/product/REQUIREMENTS.md](docs/product/REQUIREMENTS.md) (12장 시스템 아키텍처, 13장 AI 활용 명세, 14장 구현 현황 표) |
| 사용자 피드백 기록 | [docs/product/USER_TEST.md](docs/product/USER_TEST.md) 5장·6장 |
| 발표 자료 | 미등록 |
| 프로젝트 진행 기록 | [기능 명세 변경 이력](docs/product/REQUIREMENTS.md#115-변경-이력) |

### 시연 영상

실제 모바일 화면으로 13단계 조건 입력, 조합 비교, 취업 전후 지원금 비교, 공식 신청 안내를 1분 동안 시연한다. 가로형 영상에 한국어 내레이션과 자막을 넣고, 조합 비교 장면에는 가상 정책 예제임을 표시했다.

https://github.com/user-attachments/assets/04cfa122-ce12-4e66-a97c-42924c0f7a62

### 평가자 테스트 방법

기능별 확인 방법과 기대 결과는 [구현 현황 표](docs/product/REQUIREMENTS.md#14-구현-현황-표)에 있다. 표에서 말하는 '테스트 조건 A'는 아래 값이다.

<details>
<summary>테스트 조건 A · 가상 인물의 13단계 선택값</summary>

| 단계 | 고를 값 |
| --- | --- |
| 1 나이 | 25 |
| 2 지역 | 경기도 · 수원시 |
| 3 지금 상황 | 취업 준비 |
| 4 최종 학력 | 대학 졸업 |
| 5 결혼 | 미혼 |
| 6 사는 형태 | 혼자 살아요 · 없어요 (무주택) · 세대주 |
| 7 집 계약 | 월세 · 보증금 5천만 원 이하 · 월세 40만 ~ 60만 원 |
| 8 소득과 재산 | 없음 · 1억 2천만 원 이하 |
| 9 해당 조건 | 해당 없음 |
| 10 관심 분야 | 취업, 주거 |
| 11 이미 받은 정책 | 없음 |
| 12 부모님 정보 | [건너뛰기] |
| 13 조건 확인 | 앞의 선택값 확인 후 [내 조합 찾기] |

</details>

**자동 갱신 확인** (F08, 목표 03:00 KST): [정책·금리 통합 실행·스케줄 안내](backend/src/benefitfinder/jobs/DAILY_REFRESH.md)를 따른다. GitHub CI는 일일 수집 스케줄러가 아니다. Render Cron에서 2026-11-18~20 매일 03:00 KST 예약 실행 성공과 Discord 정책·기준금리 갱신 알림을 확인했다. [운영 증빙](docs/team_collaboration/README.md#2-배포-및-운영-vercelrender)을 참고한다. F12 Agent의 운영 자동 실행은 별도 검증이 필요하다.

**AI Agent 실행 기록 확인** (F12): [추출·검수·평가 CLI](backend/src/benefitfinder/jobs/README.md)를 따른다. 공유 가상 회귀와 실제 E4B 검증 요약은 관련 PR에 남기며, 실제 공고·검수·결과·로그는 로컬 `data/`에만 둔다. 제출용으로는 원문/개인정보를 제거한 실행 순서와 성공·실패 요약을 따로 확인한다.

### AI 사용 표시

- 서비스 목표: 생성된 설명에는 [AI] 표시와 근거를 붙이고, 기본 문구는 AI 생성으로 표시하지 않는다. 현재 앱은 결과 화면의 정책별 설명/근거와 이용 안내·개인정보 페이지를 제공한다. 배포 환경의 상세는 게시 정책을 조회하며 기본 로컬 환경은 가상 정책을 사용한다. 클릭 시안의 설명은 가상 예시다.
- 개발: AI 도구(Claude, Codex 등)를 사용한 커밋·PR은 [협업 절차](CONTRIBUTING.md#ai-사용-표시)에 따라 PR 본문과 커밋 트레일러에 표시했다.

## 팀원 역할과 기여

각 팀원이 **본인이 직접** 기여 요약을 쓰고, 대표 커밋·PR·문서 링크를 붙인다. 기능별 담당은 [기능 명세 9장](docs/product/REQUIREMENTS.md#9-팀원별-담당-기능-및-협업-기준)을 따른다.

| 이름 | 역할 | 기여 요약 (본인 작성) | 대표 커밋·PR·문서 |
| --- | --- | --- | --- |
| 양승현 (조장) | 통합·배포 / 백엔드 | 백엔드 아키텍처 의존성 오류 해결, LLM 제공자 연동 및 F08 Windows 호환성 개선, Vercel·Render 배포 연동 설정, 통합 테스트 오류 수정 및 PR 병합 관리 | [PR #12](https://github.com/x17545/benefitfinder-ai/pull/12), [PR #21](https://github.com/x17545/benefitfinder-ai/pull/21), [PR #56](https://github.com/x17545/benefitfinder-ai/pull/56), [커밋 a6f70c9](https://github.com/x17545/benefitfinder-ai/commit/a6f70c9) |
| 이현서 | 기획 / 화면 설계 | 주제 발전(놓친 혜택 찾기 → 최적 지원금 조합)과 문제·타깃 정의, 기획서와 기능 명세 v2.0·v3.0 작성(13단계 조건 선택, 조합 비교·상황 변화 화면, AI 기술 요소 F12~F14, 구현 현황 표). 클릭 시안·화면 설계서와 사용자 테스트 설계, 배포 사이트 흐름 점검과 기획서 대비 구현 비교 | [PR #1](https://github.com/x17545/benefitfinder-ai/pull/1) · [PR #13](https://github.com/x17545/benefitfinder-ai/pull/13) · [PR #81](https://github.com/x17545/benefitfinder-ai/pull/81) · [기획서](https://github.com/x17545/benefitfinder-ai/blob/main/docs/product/PROJECT_PLAN.md) · [기능 명세](https://github.com/x17545/benefitfinder-ai/blob/main/docs/product/REQUIREMENTS.md) · [시안](https://github.com/x17545/benefitfinder-ai/blob/main/docs/prototype/index.html) · [배포 점검·구현 비교](https://github.com/x17545/benefitfinder-ai/blob/main/docs/product/DEPLOY_CHECK.md) |
| 김하정 | 디자인 / 프론트엔드 | 화면 디자인과 웹 화면 구현(시작·조건 입력·결과·상세·상황 변화·서비스·안내 페이지), 배포 사이트 PC·모바일 점검 및 보완, 최종 제출용 팀 협업·배포·기능 테스트 증빙자료 정리 | [PR #7](https://github.com/x17545/benefitfinder-ai/pull/7), [PR #22](https://github.com/x17545/benefitfinder-ai/pull/22), [PR #28](https://github.com/x17545/benefitfinder-ai/pull/28), [PR #31](https://github.com/x17545/benefitfinder-ai/pull/31), [PR #54](https://github.com/x17545/benefitfinder-ai/pull/54), [PR #88](https://github.com/x17545/benefitfinder-ai/pull/88), [PR #89](https://github.com/x17545/benefitfinder-ai/pull/89), [PR #93](https://github.com/x17545/benefitfinder-ai/pull/93), [증빙자료](docs/team_collaboration/README.md) |
| 윤정찬 | 원천 데이터 수집·적재 | 온통청년 청년정책 API 수집·원본 보관·버전 비교 구현, 공고 HTML·PDF 텍스트 변환(notice-text-v1) 추가, 실제 수집본 비교로 키워드 순서 변경에 따른 재검수 오탐을 찾아 수정(취업·주거 해시 변경 296→8건), 수집·적재·DB PR(#39~#49) 직접 실행 리뷰, ECOS 기준금리·온통청년 3,957건·보조금24 82건 실제 수집→PostgreSQL 적재 검증, 보조금24 10,942건 조사로 취업·주거 분류값과 수집 대상 매핑 정리 | [PR #8](https://github.com/x17545/benefitfinder-ai/pull/8), [PR #33](https://github.com/x17545/benefitfinder-ai/pull/33), [PR #52](https://github.com/x17545/benefitfinder-ai/pull/52), [PR #53](https://github.com/x17545/benefitfinder-ai/pull/53), [수집 문서](backend/src/benefitfinder/ingestion/README.md) |
| 탁서윤 | 데이터 정제·AI / 자동화 | 중복 수혜 규칙 정제·검수와 원문 변경 시 재검수 처리, F12 원문 분석 Agent·문맥 보존·추출 품질 평가·검수 큐, F13 게시 근거 기반 맞춤 설명·실패 시 기본 문구 전환, F08 정책·기준금리 통합 갱신·재시도·실패 알림, Decider 정형 우선 배치 추출·원공고 기반 계산 게시 경로 구현 | [PR #9](https://github.com/x17545/benefitfinder-ai/pull/9), [PR #14](https://github.com/x17545/benefitfinder-ai/pull/14), [PR #16](https://github.com/x17545/benefitfinder-ai/pull/16), [PR #40](https://github.com/x17545/benefitfinder-ai/pull/40), [PR #69](https://github.com/x17545/benefitfinder-ai/pull/69), [분석·평가 문서](docs/development/DECIDER_PIPELINE.md), [자동 갱신 문서](backend/src/benefitfinder/jobs/DAILY_REFRESH.md) |

## 기술 스택

선정 이유는 [기획서 5.2](docs/product/PROJECT_PLAN.md#52-기술-스택과-선정-이유)에 있다.

| 영역 | 기술 |
| --- | --- |
| 화면 | React, TypeScript, Vite |
| 서버 | Python 3.12, FastAPI, Pydantic |
| DB | PostgreSQL. 게시 정책·규칙·근거 조회, 코드 판정·계산과 F13 조항 조회에 사용 |
| AI | 정형값은 코드 처리, [Decider 본문 추출](docs/development/DECIDER_PIPELINE.md)은 로컬 검증. 자격·금액 계산은 코드가 수행하며 [선택적 설명 모델](backend/src/benefitfinder/api/README.md#f13-설명-실행) 실패 시 기본 문구로 복귀 |
| 자동화 | F08 정책·금리 통합 실행·재시도·웹훅과 03:00 KST Linux systemd/macOS launchd 설정 생성. Render Cron 운영 등록·실제 정책 및 기준금리 갱신·Discord 알림 도착을 2026-11-18~20 기록으로 확인. F12 운영 자동 실행은 미검증 |
| 데이터 | 온통청년 수집·ECOS 기준금리 수동 갱신/DB 조회 구현. 보조금24 선택 ID의 API·DB·F12 연결 구현. 실제 매핑·운영 갱신은 후속 |
| 개발 도구 | uv, pnpm, GitHub Actions CI |
| 배포 | Vercel 웹·Render API 연동. [배포 서비스](https://benefitfinder-ai.vercel.app/)와 운영 DB의 실제 정책 응답 확인 기록 제공 |

## 로컬 실행

Node.js 24, pnpm 11.25.0, uv 0.9.5 이상을 준비하고 저장소 루트에서 실행한다. Python 3.12와 루트 `.venv`는 uv가 관리한다. 처음 설치하거나 실행이 안 되면 [처음 개발 시작하기](docs/development/GETTING_STARTED.md)를 따른다.

```sh
uv sync --locked
pnpm install --frozen-lockfile
pnpm dev
```

- 웹: [http://127.0.0.1:5173](http://127.0.0.1:5173)
- API 문서: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- 생존 확인: [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)

DB·LLM 키 없이 실행한다. `pip`·`npm`·`npx` 대신 uv·pnpm을 사용하고, 의존성 선언과 lockfile을 함께 관리한다.

## 결과

배포 서비스는 실제 게시 정책을 사용하며, 기본 로컬 실행은 가상 정책으로 같은 입력·조합·상황 변화·상세 흐름을 제공한다. 2026-11-17 운영 카탈로그의 계산용 정책은 36건(접수 10건·종료 26건)이며 실제 배포 API의 조건부 조합을 확인했다. 추가 신청자 사실을 받는 계산 후보 26건은 코드 배포 전 준비 단계로 구분한다. 미확인 자격과 동시 수급 관계가 남아 있어 수급 확정을 뜻하지 않는다. 집계 기준·검증 조건·잔여 제약은 [운영 게시 기록](docs/development/DECIDER_PIPELINE.md#2026-11-17-후속-처리)과 [구현 상태](docs/README.md#구현-상태)를 따른다.

정형 우선 분석은 실제 정책 2건의 동일 로컬 실행 비교에서 입력 토큰 19,053 → 1,379(92.8% 감소), 분석 시간 11.926초 → 1.330초를 기록했다. 전체 정책의 성능이나 유료 API 청구액 측정과 구분하며, [비교 조건·재현 방법](docs/development/DECIDER_PIPELINE.md#코드와-모델의-역할-분리로-줄인-분석량)을 함께 제공한다.

실사용자 평가와 제출 자료는 운영 데이터·자동 검사 결과와 별도로 기록한다.

- 기능별 구현·확인 방법: [구현 현황 표](docs/product/REQUIREMENTS.md#14-구현-현황-표)
- 사용자 테스트: 제출용 참여 인원·지표는 미등록. [관찰·피드백 기록 양식](docs/product/USER_TEST.md#6-피드백과-반영-기록-제출용)
- 대표 화면: [기능별 테스트 화면과 배포·협업 증빙자료](docs/team_collaboration/README.md)

## 개발 시작점

| 하고 싶은 일 | 먼저 볼 문서 |
|---|---|
| 어떤 제품인지 이해하기 | [기획서](docs/product/PROJECT_PLAN.md), [기능 명세](docs/product/REQUIREMENTS.md), [기획 개요·일정·제출물](docs/product/OVERVIEW.md) |
| 작업을 나누고 PR 올리기 | [협업·개발 절차](CONTRIBUTING.md), [Git 작업 절차](docs/development/GIT_WORKFLOW.md) |
| 구현 위치 정하기 | [아키텍처](docs/architecture/ARCHITECTURE.md), [웹 구조](apps/web/README.md), [백엔드 구조](backend/README.md) |
| API·데이터 연결하기 | [공용 계약](contracts/README.md), [정책 데이터 준비](docs/architecture/POLICY_DATA_PIPELINE.md) |
| 코딩 에이전트로 작업하기 | [공용 지침](AGENTS.md), 수정 경로의 하위 `AGENTS.md` |

역할과 기능별 인계의 기준은 [기능 명세 9장](docs/product/REQUIREMENTS.md#9-팀원별-담당-기능-및-협업-기준)이다. 팀원별 장기 브랜치 대신 기능별 짧은 브랜치와 PR을 사용한다. `main` 직접 작업·푸시와 `codex/` 접두어는 금지한다.

## 저장소 구조

```text
apps/web/       React + TypeScript + Vite
backend/        FastAPI, 공통 판정·분야별 규칙·데이터 준비 경계
contracts/      공용 JSON Schema와 가상 예제
docs/
  README.md     문서 지도와 구현 상태
  product/      기획 개요·기능 명세·사용자 테스트
  architecture/ 기술 설계·정책 데이터 준비
  development/  설치·컨벤션·Git·저장소 운영
  prototype/    화면 참고 HTML·PNG
  archive/      과거 기록 (현행 수락 기준 아님)
scripts/        공통 검사와 계약 타입 생성
tests/          계약·기획용 참조 계산 검증
```

## 검증

```sh
uv run --locked python scripts/check.py
```

PR 전에는 작업 브랜치에 최신 `origin/main`을 머지하고 충돌을 해결한다. 검토할 변경을 커밋한 뒤 `uv run --locked python scripts/check.py --pr`을 실행한다. 이 검사는 웹 빌드와 실제 HTTP 오류 계약을 포함하지만, 미구현 정책 기능이나 실제 PostgreSQL·LLM·정책 사실의 검증을 뜻하지 않는다. 상세 범위와 한계는 [저장소 운영 안내](docs/development/REPOSITORY_SETTINGS.md#3-현재-공통-검사의-범위)를 따른다.
