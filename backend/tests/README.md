# 백엔드 테스트

기능 구현 시 application·domain·domains·ai·ingestion·api별 테스트를 추가한다. 외부 LLM·DB 없이 검증할 수 있는 순수 규칙과 경계 테스트를 우선한다. 현재 공용 데이터 계약 검증은 저장소 루트 tests에 있다.

## 부트스트랩 API 테스트

`test_api.py`는 FastAPI 부트스트랩의 실제 HTTP 동작을 검증한다. 외부 DB·LLM·네트워크 없이 `TestClient`로 확인하며, 입력 검증·500 처리에 쓰는 가상 라우트는 테스트 안에서 `create_app()`이 반환한 앱에만 추가한다.

```bash
uv run --locked python -m unittest discover -s backend/tests -v
```

검증 범위: `GET /health` 200·`health` 계약 정합, `/docs`·`/openapi.json` 제공과 생존 확인·오류 모델의 계약 정합, 명시적인 HTTP 오류의 상태·Retry-After/WWW-Authenticate/Allow/추가 헤더 보존, JSON 본문에 맞춘 헤더 재생성, 검증 422·예상치 못한 500의 공통 오류 형식(UUID `request_id`, 민감 데이터·예외 원문 미반환), 가상 라우트의 앱 격리.
