# 백엔드 테스트

기능 구현 시 application·domain·domains·ai·ingestion·api별 테스트를 추가한다. 외부 LLM·DB 없이 검증할 수 있는 순수 규칙과 경계 테스트를 우선한다. 현재 공용 데이터 계약 검증은 저장소 루트 tests에 있다.

`test_policy_rule_review.py`는 가상 공식 문서·가짜 추출기로 근거·정책 연결·미지원 규칙·배치 상충·명시적 검수·원문 변경과 추출 실패를 검증한다. 실제 정책 사실·LLM 추출 품질·누락률·PostgreSQL·게시 연동은 검증하지 않는다.

`test_policy_rule_files.py`는 로컬 검수 JSON의 형식·메타데이터 왕복, 오류 비노출, 새 파일 저장과 실패 시 기존 파일 보존을 검증한다. `test_rule_review_workflow.py`는 검수 자료의 준비·승인·재검수 상태 전환을, `test_rule_review_jobs.py`는 `python -m benefitfinder.jobs rule-review` 명령을 가상 자료·임시 디렉터리로 검증한다. 결과 파일 생성 시각과 원문 수집·사람 검수 시각을 구분하며, 이 테스트는 운영 게시·DB 저장·매일 실행을 검증하지 않는다.

## 부트스트랩 API 테스트

`test_api.py`는 FastAPI 부트스트랩의 실제 HTTP 동작을 검증한다. 외부 DB·LLM·네트워크 없이 `TestClient`로 확인하며, 입력 검증·500 처리에 쓰는 가상 라우트는 테스트 안에서 `create_app()`이 반환한 앱에만 추가한다.

```bash
uv run --locked python -m unittest discover -s backend/tests -v
```

검증 범위: `GET /health` 200·`health` 계약 정합, `/docs`·`/openapi.json` 제공과 생존 확인·오류 모델의 계약 정합, 명시적인 HTTP 오류의 상태·Retry-After/WWW-Authenticate/Allow/추가 헤더 보존, JSON 본문에 맞춘 헤더 재생성, 검증 422·예상치 못한 500의 공통 오류 형식(UUID `request_id`, 민감 데이터·예외 원문 미반환), 가상 라우트의 앱 격리.
