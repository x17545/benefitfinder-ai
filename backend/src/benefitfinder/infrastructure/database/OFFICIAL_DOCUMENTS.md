# 공식 공고·지침·FAQ의 수집과 변경 추적

`official_documents`는 확인된 청년 취업·주거 정책에 연결한 공개 HTML·PDF 원천이다. 기존 수집 성공본·해시·변경 이력과 명시적인 `collection_sources`를 재사용한다. API 내용이 같아도 연결한 공식 문서가 변경·삭제되면 기존 트리거가 F12 초안을 무효화하고 게시 정책·F13을 내리며 재검수 이력을 보존한다. 수집이나 재등장은 승인이 아니다.

## 대상과 실행

담당자가 공식 출처·정책 연결·분야·본문 식별 문구를 확인한 계획을 Git 제외 경로에 둔다. 링크를 자동 탐색하거나 제목만으로 정책 분야를 추측하지 않는다. 계획의 `official_source_confirmed`는 수집 출처 확인이며 정책 사실·계산 규칙의 사람 검수 승인이 아니다.

다음은 가상 예제다. 모든 키를 명시한다. `external_id`는 같은 문서의 변경·삭제·재등장을 추적하는 안정된 ID다.

```json
{
  "format_version": 1,
  "documents": [{
    "external_id": "fictional-notice",
    "policy_id": "youth:fictional:employment",
    "categories": ["employment"],
    "url": "https://example.invalid/fictional-notice",
    "format": "html",
    "method": "GET",
    "form": {},
    "official_source_confirmed": true,
    "required_text": ["가상 청년 취업"],
    "content_id": "policyBody"
  }]
}
```

계획은 256KiB, 문서는 1~100개다. HTTPS의 정확한 URL만 호출하고 리다이렉트는 따르지 않는다. 공개 다운로드가 POST를 요구하면 `method: "POST"`, `form`에 확인한 공개 파일 ID를 넣는다. URL·form의 인증키·토큰·비밀번호 필드는 거부한다. 별도 API·LLM 키는 필요 없다. 원문 16MiB, HTTP 30초·최소 간격 0.5초·최대 2회 재시도 한도를 둔다.

```sh
uv run --locked python -m benefitfinder.jobs refresh-official-documents \
  --plan data/local/official-documents.json --data-dir data

uv run --locked --env-file /absolute/private/collection.env python -m benefitfinder.jobs \
  ingest-official-documents --data-dir data
```

`COLLECTION_WRITE_DATABASE_URL`이 설정돼 있으면 갱신 명령이 DB 재인계까지 수행한다. 네트워크를 다시 호출하지 않는 `ingest-official-documents`는 선택된 성공본의 모든 응답·메타데이터·원문 해시와 정규화 텍스트를 재검사한다. 어느 문서라도 응답 확인이 빠졌거나 파일이 바뀌었으면 부분 자료를 적재하지 않는다. 공개 원문도 Git에는 넣지 않으며 파일 0600·폴더 0700으로 보관한다.

`OFFICIAL_DOCUMENT_PLAN`을 설정하면 `refresh-data`가 API·금리와 함께 공식 문서를 수집한 뒤 F12 준비·분석으로 넘어간다. 이 단계의 실패를 전체 상태와 요청한 Discord 요약에 표시하고 독립적인 다른 원천은 계속 시도한다. 실제 웹훅 전송과 운영 예약 등록은 별도다.

## 본문·원본·실패

HTML은 `notice-text-v2`로 정규화한다. `form`이 본문을 감싸도 내용은 유지하고 입력 값·textarea·button·select·스크립트는 제외한다. `content_id`를 지정하면 해당 ID의 **유일하고 완전한 원본 요소**만 선택한다. ID가 없거나 중복·불완전하면 전체 페이지로 대체하지 않고 실패한다. 선택한 원본 마크업 해시에는 이미지 경로·속성도 포함된다. 바깥 조회수·메뉴 변경은 정책 내용 변경으로 세지 않으며 전체 응답과 그 해시는 계속 보관한다. 선택자를 지정하지 않으면 전체 HTML 원본 해시가 변경 기준이다. 본문 식별 문구는 선택 영역에도 있어야 한다.

PDF는 [pypdf의 layout 추출](https://pypdf.readthedocs.io/en/stable/user/extract-text.html)을 별도 프로세스에서 실행한다. 100페이지·10만 자, CPU 20초·벽시계 25초·주소 공간 768MiB 한도로 제한한다. 페이지 표식과 원문 텍스트를 보존하며 OCR이나 도표 해석은 하지 않는다. 원본 PDF 해시는 항상 변경 기준에 포함된다. 암호화·스캔·추출 실패·식별 미확인은 새 원본을 `requires_review`로 기록하고 텍스트를 비워 이전 승인을 유지하지 않도록 한다. 이 상태의 원천을 연결한 새 카탈로그 게시도 DB에서 거부한다. 임의 텍스트·금액·조건으로 대체하지 않는다.

404·410 응답은 원문과 상태를 확인한 삭제/누락이다. 타임아웃·403·429·5xx·다른 페이지·계획 오류는 수집 실패이며 마지막 전체 성공본과 확인 시각을 유지한다. 모든 설정 대상의 응답을 확인해야 성공본이 된다. 대상 ID·정책·분야의 집합 변경은 기존 저장 경로에서 거부하므로 계획 축소를 문서 삭제로 오인하지 않는다. 범위 변경은 기존 대상까지 포함한 전체 계획과 별도 데이터 경로를 검토해 수행한다.

## DB 적용·검수 연결

0011 이후 `0012_official_document_sources.sql`을 관리 역할로 명시적으로 적용한다. 앱 시작·수집 명령은 DDL을 실행하지 않는다. 기존 승인·게시·원문 이력을 보존하며 새 원천 종류만 허용한다. 쓰기 잠금 순서는 `gov24` → `official_documents` → `youthcenter` → 정책/행이다. API·문서 수집, 현금·서비스 게시와 F13 쓰기가 같은 순서를 사용한다. 수집 역할은 기존 세 수집 테이블의 쓰기 권한만 사용하며 게시·검수 테이블의 수정 권한을 추가하지 않는다.

문서 원천도 다음과 같이 전체 F12 입력과 최종 카탈로그 승인 입력에 명시적으로 연결해야 한다. 값은 가상이다. 연결되지 않은 문서로 정책 의존성을 추측하지 않는다.

```json
{"source":"official_documents","external_id":"fictional-notice",
 "material_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
```

수집·본문 변환·버전 비교·DB 변경 인계와 **명시적으로 연결된 자료**의 무효화를 제공한다. [F12 자동 준비 계획](F12_PREPARATION.md#수집한-공식-보충-문서-연결)에 원천 ID와 사용할 문서 ID를 명시하면 API 본문과 함께 등록한다. 기존 수동 문서 ID를 명시해야 그 문서를 대체하며 나머지 문서는 보존한다. 기존 API 전용 준비 계획은 빠진 원천 의존성을 자동 제거하지 않고 전체 입력 등록을 요구한다. 문서별 원천·해시·수락 시각과 현재 본문을 검증하고, 확인 시각을 현재로 꾸미거나 다른 원문의 인용을 합치지 않는다. 원문 간 상충·누락의 사람 최종 검수·계산 규칙 작성·게시는 [기존 게시 절차](F12_CATALOG.md)를 따른다.
