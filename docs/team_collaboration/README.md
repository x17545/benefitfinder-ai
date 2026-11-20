# 팀 협업 및 서비스 증빙자료

AI 혜택 코치(BenefitFinder AI) 팀이 역할을 나누어 함께 개발하고, 배포·운영하며, 실제 서비스로 기능을 확인한 과정을 캡처로 정리했습니다.

| 폴더 | 내용 | 수량 | 정리 |
|---|---|---|---|
| [Discord_capture/](Discord_capture/) | 주제 선정, 역할 분담, 회의 결과 공유, GitHub PR 협업, 개발·테스트 진행 공유 | 12장 | 김하정 |
| [Deployment_capture/](Deployment_capture/) | Vercel·Render 배포, 매일 자동 갱신(Cron Job), 운영 API, Discord 알림, DB 적재 검증 | 8장 | 양승현 |
| [Feature_test/](Feature_test/) | 배포 사이트에서 주요 기능(F01~F14)을 직접 실행한 화면 | 17장 | 김하정 |

> 캡처 속 개인 즐겨찾기, 관련 없는 대화, 민감 정보는 가림 처리했습니다.

---

## 1. 팀 협업 기록 (Discord)

팀 Discord의 `#세션-계획`, `#코딩-작업이야기` 채널에서 의견을 나누고 일정을 조율한 기록입니다.

| 파일 | 날짜 | 내용 |
|---|---|---|
| [01_프로젝트_주제선정_1.png](Discord_capture/01_프로젝트_주제선정_1.png) | 10/20 | 팀원별 프로젝트 주제 제안. 이현서님의 '놓친 혜택 찾기' 기획안(의도·사용 예시·필수 기술·데이터) 공유 |
| [01_프로젝트_주제선정_2.png](Discord_capture/01_프로젝트_주제선정_2.png) | 10/22~23 | 제안된 4개 주제로 투표 진행, '놓친 혜택 찾기'가 최종 주제로 선정 |
| [02_팀원_역할분담.png](Discord_capture/02_팀원_역할분담.png) | 10/28 | 팀원별 역할(통합·배포, 기획, 디자인·프론트엔드, 데이터 수집, 데이터 정제·AI)과 1차 MVP 목표·개발 일정 공지 |
| [03_팀회의_1106결과공유.png](Discord_capture/03_팀회의_1106결과공유.png) | 11/6 | 멘토링 회의 결과 공유. 작업별 마감일 설정, 팀원 간 일정 조율, Codex Skills 활용 방향 |
| [03_팀회의_1115결과공유.png](Discord_capture/03_팀회의_1115결과공유.png) | 11/15 | 팀회의 결과 공유. 팀원별 작업(증빙 캡처, Render 배포 환경, README 기여 작성)과 GitHub 협업 방식 |
| [04_GitHub_PR협업_1.png](Discord_capture/04_GitHub_PR협업_1.png) | 10/28 | 프론트엔드 디자인 시안 3종 공유와 기획 담당자 검토 요청, 팀원 의견 반영(네이비 옐로 선정) |
| [04_GitHub_PR협업_2.png](Discord_capture/04_GitHub_PR협업_2.png) | 10/26 | 기획 문서 v2.0 PR(#1) 공유와 리뷰 요청, 수정 방법·다음 회의 안건 안내 |
| [05_배포환경_논의.png](Discord_capture/05_배포환경_논의.png) | 11/8 | Vercel·Render 배포 완료 공유, 배포 후 발견한 지역 목록 오류의 원인과 해결 계획(PR #19) |
| [06_서비스_테스트_1.png](Discord_capture/06_서비스_테스트_1.png) | 11/4 | 1차 MVP 통합 테스트 결과. 정상 동작 기능과 추가 개발·연동이 필요한 부분 공유 |
| [06_서비스_테스트_2.png](Discord_capture/06_서비스_테스트_2.png) | 11/11 | PR #30(상황 변화·정책 상세 연결) 병합 후 배포 사이트 연동 테스트 결과 |
| [07_최종제출_준비_1.png](Discord_capture/07_최종제출_준비_1.png) | 11/15 | 보고서·평가 준비 항목, 11/17 테스터용 서비스 완성·11/19 보고서 제출 일정, 협업 안내 |
| [07_최종제출_준비_2.png](Discord_capture/07_최종제출_준비_2.png) | 11/15~16 | 사용자 테스트 설문(구글 폼) 공유, 오류 수정 후 재확인 요청 |

---

## 2. 배포 및 운영 (Vercel·Render)

**운영 흐름:** GitHub `main` 병합 → Vercel(프론트엔드)·Render(백엔드) 자동 배포 → Render Cron Job이 매일 03:00(KST) 데이터 갱신 → Discord 알림 전송 및 PostgreSQL 적재

| 파일 | 확인 내용 |
|---|---|
| [01_Vercel_Production_배포성공.png](Deployment_capture/01_Vercel_Production_배포성공.png) | `main` 브랜치(PR #92 병합, 커밋 `6f611bb`) 기준 Vercel Production 배포가 **Ready** 상태임 |
| [02_Vercel_도메인연결_정상.png](Deployment_capture/02_Vercel_도메인연결_정상.png) | 서비스 도메인 `benefitfinder-ai.vercel.app`이 Production에 연결되어 **Valid Configuration** 상태임 |
| [03_Render_백엔드_배포성공_복구확인.png](Deployment_capture/03_Render_백엔드_배포성공_복구확인.png) | 2026-11-20 Render 백엔드가 Production 배포에 성공(**Live**)했고, 14:23 일시적인 헬스 체크 실패 후 14:24 자동 복구됨 |
| [04_Render_Cron_매일03시_실행설정.png](Deployment_capture/04_Render_Cron_매일03시_실행설정.png) | Cron Job `benefitfinder-daily-refresh`에 매일 03:00(KST) 데이터 갱신 및 Discord 알림 명령이 등록됨<br>※ 화면의 스케줄 `0 18 * * *`는 UTC 18:00으로, 한국 시간 03:00에 해당 |
| [05_Render_Cron_3일연속_자동실행성공.png](Deployment_capture/05_Render_Cron_3일연속_자동실행성공.png) | 2026-11-18~20 매일 03:00(KST)에 Cron Job이 자동(Scheduled) 실행되어 3회 모두 성공함 |
| [06_Render_운영API_정상응답.png](Deployment_capture/06_Render_운영API_정상응답.png) | 배포된 백엔드의 `/api/v1/meta` API가 정상 응답하며, 실제 데이터 모드(`virtual_data: false`)와 기준금리 검증 상태(`base_rate_status: verified`)가 확인됨 |
| [07_Discord_F08_자동갱신_3일연속성공.png](Deployment_capture/07_Discord_F08_자동갱신_3일연속성공.png) | 2026-11-18~20 Cron Job 실행 결과가 Discord `#benefitfinder-알림` 채널로 자동 전송됨. 온통청년 정책과 ECOS 기준금리 갱신이 모두 성공으로 보고됨 |
| [08_PostgreSQL_정책적재_검증결과.png](Deployment_capture/08_PostgreSQL_정책적재_검증결과.png) | 2026-11-19 데이터 수집·적재 점검 결과. 활성 정책 4,016건이 최근 수집 4,016건과 일치하고, 중복 0건, 공식 원문 45건이 적재됨 |

---

## 3. 기능별 테스트 화면 (배포 사이트)

배포 사이트 [benefitfinder-ai.vercel.app](https://benefitfinder-ai.vercel.app/)에서 주요 기능을 직접 실행하고 캡처했습니다. (2026-11-20)

**입력 조건:** 저장소 README의 '테스트 조건 A'(만 25세 · 경기도 수원시 · 취업 준비 · 혼자 사는 무주택 세대주 · 월세)를 사용했습니다. 11~13번은 여러 지원금의 조합이 계산되는 화면을 보여주기 위해 **지역만 부산광역시 기장군**으로 바꿔 실행했습니다. 배포 사이트는 실제 게시 정책으로 계산하므로 결과는 정책 게시 상태에 따라 달라질 수 있습니다.

| 파일 | 화면 | 확인 내용 | 기능 |
|---|---|---|---|
| [01_S1_시작화면.png](Feature_test/01_S1_시작화면.png) | S1 시작 | 필수 동의와 '이 기기에 내 조건 기억하기' 선택 후 시작 | F01, F09 |
| [02_S2_1단계_나이입력.png](Feature_test/02_S2_1단계_나이입력.png) | S2 조건 입력 | 1/13단계 나이 입력 | F01 |
| [03_S2_2단계_지역선택.png](Feature_test/03_S2_2단계_지역선택.png) | S2 조건 입력 | 공식 지역 목록에서 시·도, 시·군·구 선택 | F01 |
| [04_S2_6단계_사는형태.png](Feature_test/04_S2_6단계_사는형태.png) | S2 조건 입력 | 사는 형태·무주택·세대주 선택과 용어 설명 | F01 |
| [05_S2_13단계_조건확인.png](Feature_test/05_S2_13단계_조건확인.png) | S2 조건 확인 | 13단계 요약에 선택한 값이 모두 표시됨 | F01 |
| [06_S2_13단계_수정반영.png](Feature_test/06_S2_13단계_수정반영.png) | S2 조건 확인 | 요약에서 지역을 눌러 수정한 값이 반영됨 (수원시 → 창원시) | F01 |
| [07_L_분석진행.png](Feature_test/07_L_분석진행.png) | L 분석 진행 | 조합 계산·서비스 확인·근거 정리 단계가 순서대로 체크됨 | F02, F03 |
| [08_S3_결과_개별정책.png](Feature_test/08_S3_결과_개별정책.png) | S3 결과 | 테스트 조건 A 결과. 함께 묶을 정책이 없어 개별 예상 지원금 표시 | F02, F05 |
| [09_S3_기간_현재가치.png](Feature_test/09_S3_기간_현재가치.png) | S3 결과 | [2년 기준]과 [현재가치로 보기]를 적용해 금액이 바뀜 (360만 원 → 약 357만 원) | F05 |
| [10_S3_조건미충족_이유.png](Feature_test/10_S3_조건미충족_이유.png) | S3 결과 | 조건이 맞지 않는 정책 123개와 미충족 조건(✕ 지역) 표시 | F04 |
| [11_S3_조합비교.png](Feature_test/11_S3_조합비교.png) | S3 결과 (기장군) | 추천 조합 515만 원과 대안 비교, 포기하는 지원금 표시 | F05 |
| [12_S3_지원금_진단칩.png](Feature_test/12_S3_지원금_진단칩.png) | S3 결과 (기장군) | 지원금별 진단 칩(✓ 충족 / ? 확인 필요), 기본 안내 문구, 출처·확인일, 동시 수급 확인 필요 안내 | F04, F07 |
| [13_결과이미지_저장.png](Feature_test/13_결과이미지_저장.png) | 결과 이미지 | [결과 이미지로 저장]으로 받은 PNG. 조합 카드와 지원금 목록만 담기고 입력 조건은 포함되지 않음 | F10 |
| [14_S4_상황변화_취업하면.png](Feature_test/14_S4_상황변화_취업하면.png) | S4 상황 변화 | [취업하면] 선택 시 현재·변화 후 총액과 실제 순증가 비교 | F06 |
| [15_S5_정책상세.png](Feature_test/15_S5_정책상세.png) | S5 정책 상세 | 지원 내용, 함께 받을 수 없는 정책, 신청 방법, 준비 서류, 출처와 [신청하러 가기] | F07 |
| [16_S6_서비스.png](Feature_test/16_S6_서비스.png) | S6 서비스 | 돈 말고 받을 수 있는 도움 목록. 환산 금액은 현금 총액과 분리 | F14 |
| [17_S1_지난조건_기억.png](Feature_test/17_S1_지난조건_기억.png) | S1 재접속 | 다시 접속했을 때 '지난번 조건으로 바로 보기' 카드와 [내 정보 지우기] 표시 | F09 |

> 배포 환경에서는 AI 생성 설명을 사용하지 않아, 결과 화면에는 F13의 기본 안내 문구가 표시됩니다(12번).
