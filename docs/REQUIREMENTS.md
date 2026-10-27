# AI 혜택 코치 (BenefitFinder AI)
## 기능 요구 명세서 (현행 진입점)

- 문서 상태: 현행 진입점 (2026-10-27 사용자 요청 반영)
- 기획 원본 작성자: 이현서 / 채택 근거: 2026-10-27 사용자의 v2 기획 유지 요청
- 현행 상세 기준: [REQUIREMENTS_v2.md](../REQUIREMENTS_v2.md) v2.1 (기획 범위 v2.0 유지)
- 현행 기획: [PROJECT_PLAN.md](PROJECT_PLAN.md)
- 기록 보존: 2026-10-26 이전 요구명세 원문은 [REQUIREMENTS_v1_ARCHIVE.md](REQUIREMENTS_v1_ARCHIVE.md)에 그대로 둔다. 아카이브는 현행 결정이 아니다.

이 문서는 결정이 아니라 안내이다. 두 요구명세가 권한 경쟁을 하지 않도록, 구현과 검증의 기준은 REQUIREMENTS_v2.md 하나로 둔다. 화면과 데이터 형식, 기능별 처리 규칙을 이 문서에서 따로 정의하지 않는다.

옛 기준의 효력: 자연어 문장 입력과 AI 조건 추출, 관련 정책 묶음 중심의 Must 8개와 Should, Could 단계화는 현행 기준이 아니다. 해당 내용은 아카이브에만 남긴다. 이 문서 어디에서도 옛 자연어 필수와 Should, Could 단계화를 기준으로 삼아 지시하지 않는다.

---

## 1. 이 문서를 읽는 법

| 사람 | 먼저 읽을 곳 |
|---|---|
| 전체 흐름 파악 | [PROJECT_PLAN.md](PROJECT_PLAN.md) 1장부터 5장 |
| 화면 설계와 문구 | [REQUIREMENTS_v2.md](../REQUIREMENTS_v2.md) 4장 |
| 기능별 처리 규칙과 완료 기준 | [REQUIREMENTS_v2.md](../REQUIREMENTS_v2.md) 5장 |
| 데이터 형식과 API | [REQUIREMENTS_v2.md](../REQUIREMENTS_v2.md) 6장 (필드 이름과 자료형은 다음 회의 확정 대상, 현재 개발 초안 기준) |
| 품질과 안전, 테스트 | [REQUIREMENTS_v2.md](../REQUIREMENTS_v2.md) 7장과 8장 |
| 담당과 협업 | [REQUIREMENTS_v2.md](../REQUIREMENTS_v2.md) 9장, [CONTRIBUTING.md](CONTRIBUTING.md) |
| 사용자 테스트 진행 | [USER_TEST.md](USER_TEST.md) |

---

## 2. 현행 기능 범위 F01부터 F11

PR1의 기능은 모두 구현한다. 우선순위 구분 없이 F01부터 F11까지가 현행 범위이다.

| ID | 기능명 | 상세 위치 |
|---|---|---|
| F01 | 조건 선택 입력 | REQUIREMENTS_v2.md 4.3, 5장 F01 |
| F02 | RAG 정책 검색 | REQUIREMENTS_v2.md 5장 F02 |
| F03 | AI Agent | REQUIREMENTS_v2.md 5장 F03 |
| F04 | 자격 진단 | REQUIREMENTS_v2.md 5장 F04 |
| F05 | 최적 조합 | REQUIREMENTS_v2.md 5장 F05 |
| F06 | 상황 변화 분석 | REQUIREMENTS_v2.md 5장 F06 |
| F07 | 정책 상세와 신청 안내 | REQUIREMENTS_v2.md 5장 F07 |
| F08 | 매일 자동 갱신 | REQUIREMENTS_v2.md 5장 F08 |
| F09 | 이 기기에 기억하기 | REQUIREMENTS_v2.md 5장 F09 |
| F10 | 결과 이미지 저장 | REQUIREMENTS_v2.md 5장 F10 |
| F11 | 배포와 통합 | REQUIREMENTS_v2.md 5장 F11 |

핵심 수치 기준: 대상 나이 만 19세부터 39세, 13단계 선택 입력, 추천 1개와 대안 2개, 기간 1년과 2년, 전체, 현재가치 할인율은 한국은행 기준금리, 전국 수집과 30개 검수 시작, 하루 사용 상한 서버 전체 100회.

---

## 3. 기능 ID 대응 기록

구 식별자와 현행 식별자의 대응을 남긴다. 대응 기록은 식별자 추적용이며, 구 기능 정의가 현행 기준이 되는 것은 아니다.

| 구 ID | 구 기능명 | 현 ID |
|---|---|---|
| M01, M02 | 자연어 상황 입력, 조건 추출과 보완 | F01 |
| M03 | RAG 정책 검색 | F02 |
| M04 | AI Agent 도구 활용 | F03 |
| M05 | 자격 진단과 결과 카드 | F04 |
| M06 | 혜택 포트폴리오 | F05 |
| M07 | 신청 준비 안내 | F07 |
| M08 | 웹서비스 배포와 통합 | F11 |

새로 들어온 현행 기능: F06 상황 변화 분석, F08 매일 자동 갱신, F09 기억하기, F10 결과 이미지 저장. 구 Should와 Could 항목은 현행 기준으로 쓰지 않는다.

---

## 4. 현행 원본 위치

| 내용 | 원본 |
|---|---|
| 현행 기획 | [PROJECT_PLAN.md](PROJECT_PLAN.md) |
| 현행 상세 요구 | [REQUIREMENTS_v2.md](../REQUIREMENTS_v2.md) |
| 아키텍처와 모듈 경계 | [ARCHITECTURE.md](ARCHITECTURE.md) |
| 협업과 리뷰 절차 | [CONTRIBUTING.md](CONTRIBUTING.md), [GIT_WORKFLOW.md](GIT_WORKFLOW.md) |
| 교환 형식과 가상 예제 | [데이터 계약](../contracts/README.md) |
| 사용자 테스트 진행 | [USER_TEST.md](USER_TEST.md) |
| 옛 기획서와 요구명세 | [PROJECT_PLAN_v1.1_ARCHIVE.md](PROJECT_PLAN_v1.1_ARCHIVE.md), [REQUIREMENTS_v1_ARCHIVE.md](REQUIREMENTS_v1_ARCHIVE.md) |

계약과 기술 정렬 상태는 현재 개발 초안 기준이며 기획자 최종 승인으로 쓰지 않는다. 6장 필드 이름과 자료형, 11.2 임시 기본값, LLM과 임베딩 모델은 다음 회의 확정 대상이다.

---

## 5. 변경 이력

| 날짜 | 내용 |
|---|---|
| 2026-10-26 | 요구명세 v1.0 초안 작성 (원문은 아카이브로 보존) |
| 2026-10-27 | 사용자 요청에 따른 v2 기획 반영: 이 문서를 현행 진입점으로 정리하고, 상세 기준을 REQUIREMENTS_v2.md v2.1 하나로 둠. 옛 Must, Should, Could 단계화는 현행 기준에서 제외 |
