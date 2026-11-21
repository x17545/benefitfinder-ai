# F12 Agent·F13 RAG 단위 테스트 검증 기록 (2026-11-21)

## 1. 검증 개요

- 프로젝트: BenefitFinder AI
- 검증 기준: GitHub main 커밋 `b8f51f5`
- 검증 환경: Windows PowerShell, Python 3.12.16, uv
- 검증 방식: Python unittest 단위 테스트
- 운영 DB 변경 및 외부 LLM API 호출: 수행하지 않음

이 문서는 F12·F13의 코드 수준 검증 결과를 기록한다. 실제 LLM을 이용한 Agent 실행, 운영 모델을 통한 RAG 설명 생성 및 운영 배포 검증을 완료했다는 의미는 아니다.

## 2. F12 정책 원문 분석 Agent

- 테스트 파일: `backend/tests/test_policy_analysis_agent.py`
- 테스트 수: 26개
- 최종 반복 검증: 전체 테스트 5회 연속 성공
- 최종 반복 검증 종료 코드: 각 실행 0
- 테스트 입력: 가상 문서, ScriptedChat 등 모의 모델 호출

확인한 주요 항목:

1. 여러 문서의 조건·금액·중복 제한 추출
2. 원문 조회 및 인용 범위 검증
3. 누락 항목 검사와 `needs_review` 처리
4. 다중 도구 호출 결과와 실행 추적 구조 검증
5. 모델 호출 횟수·시간 제한 관련 테스트

### 간헐적 실패 이력

2026-11-21 로그 저장을 위한 재실행에서 `test_timeout_model_call_limit_and_response_call_limit_are_fixed`가 1회 실패했다.

- 기대: `len(slow.messages) == 1`
- 실제: `len(slow.messages) == 2`
- 해당 실행: 26개 중 25개 성공, 1개 실패
- 이후 해당 테스트 단독 실행: 성공
- 이후 전체 26개 테스트 5회 반복: 전부 성공

실패의 정확한 원인은 아직 확정되지 않았다. 간헐적 실패 가능성을 해결했다고 주장하지 않는다.

## 3. F13 근거 기반 맞춤 설명(RAG)

- 테스트 파일: `backend/tests/test_grounded_explanation.py`
- 테스트 수: 12개
- 확인 결과: 12개 모두 성공
- 테스트 입력: 가상 게시 조항, 메모리 검색 저장소, 모의 LLM 응답

확인한 주요 항목:

1. 게시된 원문 조항 검색 및 프롬프트 포함
2. 다른 정책·다른 버전·검수 초안 제외
3. 설명의 근거와 정책명·금액·판정 검증
4. 잘못된 인용 또는 숫자에 대한 기본 문구 전환
5. 허용하지 않은 정보의 모델 입력 제외

## 4. 재현 명령어

F12:

```powershell
uv run --locked python -X utf8 -m unittest discover -s backend/tests -p "test_policy_analysis_agent.py" -v
```

F13:

```powershell
uv run --locked python -X utf8 -m unittest discover -s backend/tests -p "test_grounded_explanation.py" -v
```

## 5. 검증 범위 및 추가 증빙

| 항목 | 현재 확보한 증빙 | 아직 확인이 필요한 항목 |
|---|---|---|
| F08 자동화 | Render Cron 운영 성공 및 Discord 알림 기록 | F12 운영 자동 실행 여부 |
| F12 Agent | 26개 단위 테스트, 5회 반복 성공 | 실제 LLM 도구 자율 선택 및 다단계 실행 추적 |
| F13 RAG | 12개 단위 테스트 성공 | 실제 모델 응답과 검색 근거 연결 사례 |

F12·F13 실제 모델 실행 기록은 담당자 자료를 별도로 확인한 뒤 보완한다. 단위 테스트 성공을 실제 모델 실행이나 전체 기능 수락 완료로 표현하지 않는다.

## 6. 원본 로그

원본 테스트 로그는 로컬 `data/submission-evidence/`에 보관한다. `data/`는 Git 제외 경로이며, 원본 로그를 GitHub에 공개하지 않는다.

담당자: 양승현 (백엔드·통합·배포 검증)