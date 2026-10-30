# 데이터 수집·정제

작업 전에는 루트 지침과 이 경로의 [추가 에이전트 지침](AGENTS.md)을 함께 읽는다.

현재 구현: 온통청년 정책 목록의 원천 수집·원본 보관·버전 비교(`youthcenter.py`, `versioning.py`, `collect.py`). 네트워크·원본 저장·버전 저장은 `application/collection.py`의 인터페이스로 주입받는다. 공용 DB가 정해지기 전까지는 infrastructure의 HTTP 어댑터와 로컬 JSON 파일 저장소로 실행한다. 스케줄·재시도·알림, 공고 원문 수집, 정규화·검수, PostgreSQL 저장은 아직 구현하지 않았다.

- `collect_youthcenter()`는 모든 응답을 해석 없이 스냅숏(요청 파라미터·수집 시각·상태·SHA-256)으로 보관하고 정책 ID별 레코드 해시로 신규·변경·유지·삭제를 구분한다.
- API 키는 요청 파라미터·스냅숏·오류에 넣지 않는다. 어댑터가 직접 추가하고, 어댑터 예외는 형식 이름만 기록한다.
- 페이지·레코드 오류(응답 상태, 형식, ID 누락, 같은 ID의 다른 내용, 페이지 상한)는 성공 데이터와 분리한다. 하나라도 실패하면 `complete=False`이며 삭제 감지를 하지 않는다.
- 2026-10-30 실제 응답 표본으로 확인한 설정은 `YOUTHCENTER_POLICY_LIST`다. 정책 ID는 `plcyNo`, 목록은 `result.youthPolicyList`, 전체 건수는 `result.pagging.totCount`이고 `resultCode`가 200이 아니면 실패로 처리한다. 테스트는 같은 필드 이름에 가상 값을 넣는다.
- 조회수(`inqCnt`)는 정책 내용과 무관하게 바뀌므로 변경 판정 해시에서 뺀다. 담당자 이름(`sprvsnInstPicNm`, `operInstPicNm`)은 수집 레코드에 저장하지 않는다. 원본 스냅숏에는 응답 전체가 남으므로 원본 보관 위치·기간은 개인정보 기준으로 별도 정한다.
- 한 페이지 최대 건수는 아직 확인하지 않았다. 서버가 요청보다 적게 주면 `short_page` 오류로 멈추고 누락을 정상 완료로 보지 않는다.

원천 API 응답의 파싱·정규화·중복 처리·품질 검증과 게시 흐름을 둔다. API만으로 확인되지 않는 중복 제한·예외는 공식 공고·지침·FAQ를 보완 수집하고 Agent 추출 인터페이스로 연결한다. 조건·방향·기간·예외·금액 영향·근거 버전을 보존한 후보를 검증·사람 검수 후 게시한다. 상세는 [정책 데이터 분석·정제](../../../../docs/architecture/POLICY_DATA_PIPELINE.md)를 따른다.

네트워크·저장 어댑터는 주입받고 실행 주기는 jobs가 담당한다. API 필드 누락을 동시 수급 허용으로 해석하지 않으며, 미지원·상충·불명확은 검수 대기로 남긴다. 실제 원본·정제 결과는 Git 제외 경로에서 다룬다.

윤정찬은 API·공식 원문 수집·원본/출처/버전 관리와 PostgreSQL 적재를, 탁서윤은 데이터 정제·조건 구조화·품질 검증과 ai 원문 분석·중복 수혜 규칙 추출·자동화를 맡는다. 서윤의 정제·검수 결과를 정찬이 원본·검수 대기·게시 상태와 구분해 적재하고, 양승현이 게시된 조건·금액·중복 제한·근거를 API·판정·조합 계산에 연결한다. 수집·적재 실행 함수와 실패·재검수 결과는 서윤의 jobs 자동화로 연결한다. 구체 DB 쓰기는 infrastructure 어댑터에 두며, 작업은 Issue에서 파일별로 나눈다.

## 로컬 수집 실행

```bash
uv run --locked python -m benefitfinder.jobs collect-youthcenter
```

인증키는 환경 변수 `YOUTHCENTER_API_KEY`로만 전달한다. 저장소 루트에서 실행하면 아래 Git 제외 경로에 결과가 생긴다.

| 경로 | 내용 |
|---|---|
| `data/raw/youthcenter/snapshots/` | 페이지별 원본 응답과 `.meta.json`(요청 파라미터·수집 시각·상태·해시, 키 제외) |
| `data/processed/youthcenter/records.json` | 정책 ID별 최신 레코드(담당자 이름 제외), 마지막 확인 시각, 삭제 감지 시각 |
| `data/processed/youthcenter/changes.jsonl` | 신규·변경·삭제 이력 |
| `data/processed/youthcenter/records.csv` | 최신 레코드 CSV(엑셀용 UTF-8 BOM) |
| `data/processed/youthcenter/fields.csv` | 필드별 채워진 건수·서로 다른 값 수·예시. 구조 공유용 |

전체 수집에 성공하면 종료 코드 0, 일부 실패하면 성공분만 저장하고 1을 돌려준다. 요청 사이에는 0.5초 간격을 둔다. 공식 서버가 가끔 일시적인 500을 돌려주므로(2026-10-30 실행에서 확인), 같은 요청이 5xx·연결 오류면 2·4·8초 간격으로 최대 3번 다시 시도한다. 그래도 실패하면 그 페이지에서 멈추고, 다시 실행하면 받은 레코드는 `unchanged`로 이어진다. 실제 데이터 파일은 PR에 첨부하지 않고 공유 방법은 팀에서 정한다.
