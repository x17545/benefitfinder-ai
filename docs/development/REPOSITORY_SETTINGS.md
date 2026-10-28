# 에이전트와 저장소 운영 설정

## 1. 저장소에 포함한 설정

| 파일 | 역할 |
|---|---|
| [AGENTS.md](../../AGENTS.md) | Codex 등 지원 에이전트의 공용 진입 지침 |
| [웹 지침](../../apps/web/AGENTS.md), [백엔드 지침](../../backend/AGENTS.md) | 프론트엔드·백엔드 추가 지침 |
| [AI](../../backend/src/benefitfinder/ai/AGENTS.md)·[수집](../../backend/src/benefitfinder/ingestion/AGENTS.md)·[계약](../../contracts/AGENTS.md)의 `AGENTS.md` | 작업 영역별 추가 지침 |
| [CLAUDE.md](../../CLAUDE.md) | `@AGENTS.md`로 공용 원본을 불러오는 Claude Code 진입점 |
| [Cursor 규칙](../../.cursor/rules/project.mdc) | 항상 적용되는 Cursor 진입 규칙 |
| `.editorconfig` | UTF-8·LF·들여쓰기 편집 설정 |
| [PR 템플릿](../../.github/pull_request_template.md) | PR 본문에 동기화·검증·공동 검토 기록 |
| `scripts/check.py` | 로컬·CI 공통 검사 |
| `.github/workflows/ci.yml` | PR과 main에서 실행할 `repository-checks` 작업 |

이 파일들은 커밋해 공유해야 다른 팀원의 clone과 GitHub에도 적용된다. 현재 로컬 파일을 만들었다는 사실만으로 원격 보호 설정이 적용되지는 않는다.

## 2. 에이전트 최초 연결 확인

팀원이 저장소를 열고 새 대화를 시작할 때 한 번 아래를 확인한다.

> 수정하지 말고, 읽은 지침 파일과 허용 브랜치 접두어, PR 전 main 동기화 방향, 검사 명령을 설명해 줘.

공용 `AGENTS.md`와 작업 영역의 추가 지침이 로드됐는지 확인한다. Codex의 자동 발견은 시작 디렉터리의 영향을 받으므로 루트 지침에 수정 대상의 하위 지침도 직접 읽도록 명시했다. Claude Code·Cursor도 공용 원본을 참고하게 연결했지만 도구 버전·개인 설정·사용자 지시는 별도 영향을 줄 수 있다.

도구가 다르거나 지침이 읽히지 않으면 해당 도구의 시작 규칙에서 공용 `AGENTS.md`를 읽도록 연결한다. 규칙 본문을 도구별 파일에 복사해 서로 다르게 관리하지 않는다. 새 지침이 적용되지 않는 기존 세션은 새 세션에서 로드를 확인한다.

공식 참고: [Codex AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md), [Claude Code 파일 import](https://code.claude.com/docs/en/memory#import-additional-files), [Cursor Rules](https://cursor.com/docs/rules).

## 3. 현재 공통 검사의 범위

`uv run --locked python scripts/check.py`는 다음을 확인하며 Git 상태를 변경하지 않는다. 의존성 설치는 먼저 완료해야 하며, uv의 환경 동기화 과정은 필요 시 네트워크를 사용할 수 있다.

- 허용 브랜치 이름, 미완료 merge/rebase와 충돌 상태
- 대응하는 텍스트 형식의 UTF-8·LF·끝 개행·줄 끝 공백, 일반·diff3 충돌 마커, JSON/Python 문법, 로컬 문서 링크
- 실제 데이터·자격 증명 경로의 Git 유입
- 저장소 `.gitignore`로 제외한 파일의 강제 추가·기존 추적, 제외·공유 경로의 회귀 검증
- 백엔드의 직접 import 방향과 순수 업무 모듈의 표준 라이브러리 제한
- `tests/`와 `backend/tests/`에서 발견되는 unittest 테스트
- 계약·ignore·Git 작업 절차·기획용 가상 계산 정답의 필수 회귀 테스트 파일 4개 유지
- 다른 패키지 관리자 lockfile 유입·기본 프로젝트 파일 누락
- Ruff, 생성된 계약 타입의 최신 여부, 웹 lint·타입·테스트·빌드

기획용 계산 검사는 가상 정책의 정답·기간 변경·군복무 구간·조합 누락·현재가치의 기준을 확인한다. 제품 계산기나 실제 정책 판정의 구현·검증을 뜻하지 않는다.

`--pr`은 추가로 **로컬에서 마지막으로 갱신한 origin/main이 HEAD의 조상인지**와 **검증 전후 작업 트리가 깨끗한지** 확인한다. 원격이 최신인지 직접 확인하지 않으므로 먼저 `git fetch origin`이 필요하다. `--branch`는 detached HEAD인 CI가 실제 head 브랜치를 전달하는 용도다. `--allow-main`은 main의 CI 검사 전용이며 작업 브랜치 규칙을 피하려고 사용하지 않는다.

직접 import 검사는 동적 import·실제 호출의 의미·모든 외부 SDK 사용을 판별하지 못한다. 표준 라이브러리 안의 네트워크 호출까지 차단하는 샌드박스도 아니다. 파일 검사도 모든 비밀값·개인정보를 찾아내는 보안 검사를 대신하지 않는다.

`.gitignore`는 환경 변수·자격 증명, 개발 캐시·빌드 결과, 로컬 데이터·DB 볼륨·덤프·로그를 제외한다. `data/`는 `README.md`만 공유하고 가상 자료는 `contracts/fixtures/`에 둔다. `.env.example`·`uv.lock`·`pnpm-lock.yaml`·마이그레이션·공용 에이전트 지침은 계속 포함한다. JSON·SQL·CSV 전체를 제외해 소스나 예제를 숨기지 않는다.

이미 추적 중인 파일에는 `.gitignore`가 소급 적용되지 않으므로 공통 검사는 저장소의 제외 규칙에 걸리는 추적 파일도 실패로 처리한다. 검사 시 파일 내용을 출력하지 않으며, 사용자의 전역 ignore 설정은 이 차단 기준에 포함하지 않는다. 발견 시 소유와 노출 범위를 확인하고 필요한 파일만 처리한다. 이 검사는 커밋 자체를 가로막는 Git hook이 아니므로, 원격 병합 차단은 아래의 필수 CI 설정과 함께 적용한다.

공식 참고: [Git의 ignore 규칙과 이미 추적 중인 파일](https://git-scm.com/docs/gitignore).

React·TypeScript·Vite 개발 화면과 FastAPI 생존 확인·공통 오류 API가 있다. 설치는 `uv sync --locked`·`pnpm install --frozen-lockfile`이며 CI도 같은 도구를 쓴다. 실제 PostgreSQL·LLM 연동, 접근성 자동 검사, 정책 사실 검증은 아직 없다. DB 구현 시 테스트용 PostgreSQL과 마이그레이션 검증도 연결한다. 검증 결과를 이 범위 밖의 기능 완료로 해석하지 않는다.

CI는 PR의 합성 merge commit 대신 실제 head를 검사한다. 합성 commit을 쓰면 작업자가 main을 반영하지 않아도 조상 검사를 통과할 수 있기 때문이다. PR 또는 작업 브랜치의 수동 검사 직전에 main을 다시 fetch하고 `--pr`을 실행한다. `--allow-main`은 main의 CI에서만 사용한다. 검사 이후 main이 바뀌는 상황은 아래의 최신 브랜치 필수 설정과 재검증 절차로 처리한다.

공식 참고: [GitHub PR 이벤트와 merge ref](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request), [checkout](https://github.com/actions/checkout), [setup-uv](https://github.com/astral-sh/setup-uv), [pnpm setup](https://github.com/pnpm/action-setup), [setup-node](https://github.com/actions/setup-node).

## 4. 향후 관리자에게 전달할 GitHub 설정 참고

현재 계정에는 push 권한만 있어 사용자의 결정으로 main 보호 설정 적용은 이번 작업에서 제외했다. 아래 목록과 JSON은 향후 관리자용 참고 자료다. 브랜치·PR·사람의 검토 절차와 CI는 계속 사용하지만, 원격 설정으로 병합을 강제 차단한다고 가정하지 않는다. 문서·설정 파일 작성만으로 원격에 적용되지 않는다.

1. 기본 브랜치는 `main`으로 유지한다.
2. main 변경은 PR을 거치도록 하고 작성자 외 승인 1개 이상을 요구한다.
3. CI가 실제로 한 번 실행된 후 필수 상태 검사에 **`repository-checks`**를 등록한다. 워크플로 파일 이름이 아니라 실제 검사 이름을 선택한다.
4. 필수 검사는 최신 main과 호환되는 브랜치에서 통과하도록 설정하고, 새 코드 push 시 오래된 승인 재검토도 요구한다.
5. 미해결 리뷰 대화를 해결하도록 하고, main 강제 푸시·삭제를 허용하지 않는다. 관리자·봇의 불필요한 우회도 제한한다.
6. PR 반영 방식은 squash merge를 기본으로 하고, 승인 없는 자동 병합이나 배포는 활성화하지 않는다.
7. 코드 소유자 검토를 운영할 때는 팀원의 실제 GitHub ID와 권한을 확인해 `.github/CODEOWNERS`를 만들고 소유자 승인을 필수로 설정한다.

관리자용 [main 보호 설정 파일](../../.github/main-branch-protection.json)을 제공한다. 승인 1개, 새 push 시 승인 재검토, 마지막 push 작성자 외 승인, 최신 main 반영·필수 CI, 리뷰 대화 해결, 관리자에게도 규칙 적용, 강제 push·삭제 금지, 선형 이력을 설정한다. 선형 이력을 위해 PR은 squash merge로 반영한다. CODEOWNERS 승인은 실제 소유자 지정 전이므로 켜지 않는다.

CI가 한 번 실행되어 `repository-checks`가 표시된 뒤, 저장소 소유자 또는 관리자가 다음 명령을 실행한다. 단순 push 권한만 있는 계정으로는 적용할 수 없다.

```bash
gh api --method PUT repos/x17545/benefitfinder-ai/branches/main/protection \
  --input .github/main-branch-protection.json
```

적용 응답과 다음 조회에서 `strict`, `repository-checks`, 승인 수, 관리자 적용·대화 해결이 켜져 있고 강제 push·삭제가 꺼져 있는지 확인한다. 설정 파일의 존재나 API 요청 시도만으로 적용됐다고 보고하지 않는다.

```bash
gh api repos/x17545/benefitfinder-ai/branches/main/protection
```

소유자 우선 지정 대상은 `AGENTS.md`와 도구별 지침, `.github/`, `scripts/`, `contracts/`, 공통 `domain/`이다. ID가 확인되지 않아 가짜 소유자가 들어간 CODEOWNERS 파일은 만들지 않았다. 사람 이름을 GitHub ID라고 가정하지 않는다.

검사 스크립트·워크플로·테스트를 같은 PR에서 약화시키는 문제는 CI만으로 완전히 막을 수 없다. 위 소유자 리뷰와 변경 이유 검토를 함께 운영한다. CODEOWNERS 파일을 넣는 것과 소유자 리뷰를 강제하는 설정은 별개다.

공식 참고: [브랜치 보호](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches), [CODEOWNERS](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners).
