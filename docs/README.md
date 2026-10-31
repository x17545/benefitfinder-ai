# 문서 지도

문서를 찾는 첫 진입점이다. 같은 결정을 여러 파일에 복제하지 않고, 아래 표에서 정한 기준 문서를 갱신한다. 링크된 문서의 버전은 본문에서 관리하며 현행 파일명에는 버전 번호를 붙이지 않는다.

## 문서별 기준

| 주제 | 기준 문서 | 경계 |
|---|---|---|
| 제품 배경·가치·일정·제출물 | [기획 개요](product/OVERVIEW.md) | 기능·계산 규칙을 재정의하지 않음 |
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

## 작업별 읽는 순서

1. 처음 참여: [루트 README](../README.md) → [설치 안내](development/GETTING_STARTED.md) → [협업 절차](../CONTRIBUTING.md) → [Git 절차](development/GIT_WORKFLOW.md).
2. 기능 구현: `AGENTS.md` → 기능 명세의 해당 기능·화면·수락 조건 → 아키텍처 → 관련 계약 → 수정 경로의 README·하위 `AGENTS.md` → 코딩 컨벤션.
3. 데이터 수집·분석·정제: 위 공통 기준 → 정책 데이터 준비 → [ingestion](../backend/src/benefitfinder/ingestion/README.md)·[ai](../backend/src/benefitfinder/ai/README.md)·[로컬 데이터 보관](../data/README.md).
4. 검증·리뷰: 해당 기능의 수락 조건 → 공용 계약 → 협업·Git 절차 → 저장소 운영 안내의 검사 범위.

문서만 정리하는 작업도 링크된 기준과 변경 대상 경로를 확인한다. 무관한 기능 전체를 매번 읽을 필요는 없다.

## 구현 상태

2026-10-31, 현재 코드의 구현 기준이다. 배포 완료를 뜻하지 않는다.

| 항목 | 상태 |
|---|---|
| React + TypeScript strict + Vite | S1·S2 13단계·L 진행/오류·S3 조합/목록/기간별 빈 결과·검수 대기 등 보완 목록, 라우팅·추천/정책 상세 중첩 응답 검증 구현 |
| FastAPI | `GET /health`, 공통 오류·HTTP 상태/헤더 보존, OpenAPI 구현 |
| 개발 도구 | uv·pnpm, lockfile, 계약 타입 생성, 공통 검사·GitHub CI 구성 |
| 정책 API·자격 진단·조합 계산·상황 변화·정책 상세 | 웹 요청·결과 표시 있음, 서버 정책 API와 S4·S5 화면 구현 전 |
| PostgreSQL·마이그레이션 | 경계·환경 변수 예시만 있음, DB 접속·테이블 구현 전 |
| 수집·03시 자동 갱신 | 온통청년 목록 수집·원본 보관·버전 비교, 전체 건수·고유 ID 검증, 빈 결과 삭제 처리, 실패 복구가 있는 로컬 JSON·CSV 저장과 수동 실행 명령 구현. 매일 실행·알림·PostgreSQL 저장·공고 원문 수집 연결 전 |
| 데이터 Agent·규칙 검수/게시 | 추출기 인터페이스, 원문 인용·해시·버전·정책 연결 검사, 명시적 사람 검수 기록과 원문 변경 시 승인 해제 구현. 반복 갱신 시에도 미해결 사유를 보존하고 검수 대기를 유지. 조건부·기간·차감·미해결 후보는 보존 후 검수 대기. 가짜 추출기로 검증하며 실제 LLM·검수 저장/게시·계산 연동은 구현 전 |
| 기억하기·이미지 저장 | 탭 내 입력/결과·기억하기 선택 복원, 조건 변경 시 결과 무효화·손상 캐시 삭제, 선택한 조건 30일 기억하기·전체 삭제, 조합 PNG 저장 구현 |
| 배포·통계 | 기획 요구, 제품 기능 구현 전 |

아키텍처의 구성도와 기능 명세는 목표 구조·수락 조건이다. 디렉터리가 존재하거나 참조 계산 테스트가 통과해도 해당 기능이 구현된 것은 아니다. `/health`는 프로세스 생존 확인이며 DB·데이터 준비 상태를 보장하지 않는다. 구현 상태가 바뀌면 이 표도 갱신한다. 실제 동작과 검사 범위의 근거는 코드·테스트·[검사 안내](development/REPOSITORY_SETTINGS.md#3-현재-공통-검사의-범위)에서 확인한다.

기본 백엔드에는 정책 API가 없으므로 실제 추천 흐름은 아직 통합되지 않았다. 개발 모드에서만 지역 API 연결 실패 시 `[가상]` 지역과 분석 실패 후 예제 결과 버튼을 제공한다. 예제 결과는 개발용 표시를 붙이며 운영 빌드에는 예제 진입 버튼이 없다. 이용 안내·개인정보 페이지도 현재는 자리표시자다.

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
