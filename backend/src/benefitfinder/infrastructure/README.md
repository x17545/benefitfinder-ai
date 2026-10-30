# 외부 연동 어댑터

현재 구현: 온통청년 HTTP 어댑터(`youthcenter_http.py`)와 공용 DB 결정 전까지 쓰는 로컬 JSON·CSV 파일 저장소(`local_files.py`). 둘 다 `application/collection.py`의 인터페이스를 구현하며 표준 라이브러리만 사용한다. PostgreSQL 저장소는 구현하지 않았다.

PostgreSQL·검색 인덱스·LLM SDK·원천 데이터 HTTP 클라이언트 등 구체 구현을 둔다. application의 인터페이스를 구현하고 해당 분야의 판정 규칙을 포함하지 않는다.

PostgreSQL의 연결·트랜잭션과 저장 모델·저장소 구현은 [database/](database/README.md)에 둔다. ORM·드라이버는 아직 선택하지 않았다. DB 마이그레이션은 `backend/migrations/versions/`에 둔다.
