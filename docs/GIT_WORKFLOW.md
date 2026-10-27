# Git 협업 절차 (GIT_WORKFLOW)

이 문서는 5명 초보 팀과 코딩 에이전트가 함께 따르는 Git 절차다.
예시 명령은 상황별로 나눠져 있다. 통째로 복사해 한 번에 실행하지 않는다.
각 코드블록은 해당 상황에서만 순서대로 실행한다.

## 1. 핵심 원칙

1. main에서 직접 작업하지 않고 main에 직접 push하지 않는다.
2. 한 작업은 하나의 짧은 브랜치와 하나의 PR로 처리한다. main 외 장기 통합 브랜치는 만들지 않는다.
3. 새 작업 전 상태를 먼저 확인한다. 다른 사람의 미커밋 변경을 임의로 commit, stash, reset하지 않는다.
4. 완료 전에는 작업 브랜치에서 최신 origin/main을 merge해 온다. 방향을 바꾸지 않는다.
5. 충돌은 파일별로 의미를 읽고 해결한다. 무조건 ours, theirs로 덮어쓰지 않는다.
6. 검사 실패는 원인을 고친다. skip으로 통과시키지 않는다.
7. 원격 동기화가 안 되면 완료나 최신이라고 주장하지 않고 현재 상태를 보고한다.

로컬 검사 명령과 CI 파일은 저장소에 포함한다. GitHub의 필수 검사·리뷰·main 보호 설정은 관리자가 별도로 적용한다. [운영 설정](REPOSITORY_SETTINGS.md)을 따른다.

## 2. 초보용 개념 정리

- commit: 변경 중 선택한 부분을 하나의 기록으로 묶는 일이다. 내 컴퓨터에만 남는다.
- push: 로컬 commit을 원격 저장소(origin)에 올리는 일이다. push 전에는 다른 사람이 볼 수 없다.
- merge: 한 브랜치의 기록을 다른 브랜치에 합치는 일이다. 어느 쪽을 어느 쪽에 합치는지 방향이 중요하다.
- PR: 작업 브랜치를 main에 합쳐 달라고 요청하는 화면이다. 리뷰와 CI가 붙는 자리다.

- 평소 방향: 작업 브랜치에 머물면서 origin/main을 merge해 온다(최신화).
- 반영 방향: 작업 브랜치를 main에 올릴 때는 PR로만 올린다(직접 merge와 push 금지).

이 팀은 squash merge를 권장한다. 브랜치의 여러 commit이 main에는 하나로 정리된다.
PR마다 리뷰 1명과 CI 통과가 필수다. PR을 올린 뒤 main이 바뀌면 다시 동기화하고 다시 검증한다. 다른 사람이 작성한 기존 PR에 변경을 추가하면 작성자 제목·본문은 보존하고, 변경 내용·이유·검증은 별도 리뷰 댓글로 기록한다.

## 3. 브랜치 이름 규칙

허용 접두어는 아래 7개뿐이다. 형식은 `<접두어>/<작업명>`이며 작업명은 소문자 영문·숫자 묶음을 하이픈 하나로 연결한다. 공백·밑줄·추가 슬래시·앞뒤 하이픈은 금지한다.

| 접두어 | 사용 상황 |
|---|---|
| `feat/` | 새 사용자 기능 |
| `fix/` | 버그 수정 |
| `docs/` | 문서·기획 정리 |
| `refactor/` | 동작을 유지하는 코드 구조 개선 |
| `test/` | 테스트·평가 개선 |
| `chore/` | 개발 환경·일반 유지보수 |
| `ci/` | 자동 검사·CI 설정 |

좋은 예: `feat/ai-policy-search`, `fix/youth-age-filter`, `docs/git-workflow`

금지: `codex/...`, `feature/...`, 개인 이름 접두어(예: `person/...`)는 쓰지 않는다.
브랜치는 짧게 유지하고 한 브랜치에 여러 작업을 섞지 않는다.

## 4. 커밋 메시지 규칙

형식은 type(scope): 한국어 설명이다. scope는 생략할 수 있다.
type은 브랜치 접두어와 같은 7개를 쓴다.

```text
feat(search): 청년 정책 검색 조건에 나이 필터 추가
fix(contract): 혜택 응답 예제의 필수값 누락 수정
docs(workflow): Git 협업 절차 문서 추가
```

제목은 한국어로 짧게 쓰고, 필요하면 왜 바꿨는지 본문에 한두 줄로 적는다.

## 5. 새 작업 시작 전 확인

다른 사람 작업과 엉키지 않기 위한 확인이다. 새 작업 전에 항상 한다.

```bash
git status
git branch --show-current
git remote -v
git log --oneline -5
```

`git status`에 merge·rebase·cherry-pick이 진행 중이라고 나오면 새 작업을 시작하지 않고 그 작업부터 해결한다. `.git`은 worktree에서 파일일 수 있으므로 폴더라고 가정하지 않는다.
다른 사람의 미커밋 변경이 보이면 임의로 commit, stash, reset하지 않고 주인에게 묻는다.
같은 작업 브랜치가 있으면 이름·변경 내용을 확인해 재사용한다. `main`의 미커밋 변경은 소유가 확인된 작업만 보존해 작업 브랜치로 옮긴다. 정리가 불가능하면 별도 clone/worktree로 분리한다.

## 6. 새 작업 브랜치 만들기

상태가 깨끗하고 미완료 merge가 없을 때만 만든다. 원격 main 기준으로 만든다.

```bash
git fetch origin
```

```bash
git switch --no-track -c feat/ai-policy-search origin/main
```

--no-track을 꼭 붙인다. 작업 브랜치의 upstream이 원격 main으로 잘못 잡히는 일을 막는다.
feat/ai-policy-search 자리는 이번 작업 이름으로 바꾼다. 이름 규칙은 3장을 따른다.

이미 같은 작업 브랜치가 있고 작업 트리가 깨끗하면 새로 만들지 않고 전환한다.

```bash
git switch feat/ai-policy-search
```

해당 작업의 브랜치인지 확인한 뒤 8장의 main 동기화를 수행한다. 다른 작업의 변경을 섞지 않는다.

## 7. 작업 중 커밋하기

현재 작업과 관계있는 파일만 골라서 stage하고 commit한다.

```bash
git status --short
```

```bash
git add <명시-경로-1> <명시-경로-2>
git commit -m "type(scope): 한국어 설명"
```

명시 경로는 실제 파일 경로로 바꾼다(예: git add docs/GIT_WORKFLOW.md).
커밋 전 `git diff --cached`로 실제 포함 내용을 읽고, 전후 `git status`로 범위를 확인한다. 남의 파일이 섞였으면 해당 경로만 unstage하고 보존한다. `git add .`, `git add -A`, `git commit -a`는 사용하지 않는다.

## 8. 완료 전 main 동기화

commit을 마친 뒤 작업 브랜치에 그대로 머문 상태에서 최신 main을 가져와 합친다. 방향을 엄수한다.

```bash
git fetch origin
```

```bash
git merge --no-edit origin/main
```

origin/main을 내 작업 브랜치로 merge하는 방향만 허용한다.
내 작업 브랜치를 main 쪽에 merge하거나 로컬 main을 강제로 맞추지 않는다.

## 9. 충돌 해결

충돌이 나면 파일별로 본인 변경과 상대 변경의 의미를 읽고 합친다.
공유 계약(contracts, 공통 모델)이 걸린 충돌은 섣불리 판단하지 않고 담당자에게 묻는다.
그 외에는 스스로 해결하고, 필요한 공유 계약 판단만 질문한다.
무조건 ours, theirs로 덮어쓰는 방식은 금지다.

```bash
git status --short
git diff --check
```

파일을 직접 열어 양쪽 내용을 합치고 충돌 마커를 제거한다. 마커가 남아 있으면 아직 해결 중이다. 전체 파일을 한쪽 버전으로 덮어쓰지 않는다.
해결한 파일을 명시해서 add하고 commit한다.

```bash
git add <해결한-파일-경로>
git diff --cached --check
git commit --no-edit
```

```bash
git status
```

남은 충돌이 없고 merge가 종료됐는지 확인한다. 소유가 확인되지 않은 변경을 버려 clean을 만들지 않는다. merge commit을 마친 뒤 아래 전체 검사와 실제 기능 검증을 수행한다. 공통 검사는 미완료 merge를 허용하지 않는다.

## 10. 검사

로컬 전체 검사는 아래 명령이다.

```bash
uv run --locked python scripts/check.py
```

PR 전 검사는 아래 명령이다. 작업트리 clean 여부와 origin/main이 HEAD의 조상인지도 확인한다.

```bash
uv run --locked python scripts/check.py --pr
```

--pr은 fetch하지 않는다. 그래서 직전에 git fetch origin이 필수다.
검사가 오래 걸리거나 추가 commit 뒤에 main이 바뀌었을 수 있다.
그때는 다시 fetch하고, 필요하면 8장의 merge를 한 뒤 검사를 다시 한다.
실패는 진짜 원인을 고치고, skip으로 통과시키지 않는다.

## 11. push와 PR 만들기

검사가 끝난 뒤 작업 브랜치를 원격에 올린다.

```bash
git push -u origin HEAD
```

같은 head, base가 main인 기존 PR이 있는지 먼저 확인한다. 없으면 새로 만든다.
base는 항상 main으로 명시한다. GitHub 연결 도구 또는 설치된 GitHub CLI를 사용한다. 기존 PR 조회가 안 되면 중복 생성하지 않고 차단 사유를 보고한다.
PR에는 목적·변경 결과·공동 검토 대상, 마지막 fetch의 main 커밋과 검증한 작업 브랜치 커밋, 실행한 검사와 미실행 검사를 적는다. UI 변경이면 확인 방법도 남긴다.

사용자 요청으로 구현한 작업은 정상적인 commit, push, PR 작성까지 수행한다.
다만 자동 merge, 승인 대리, 배포, 보호 설정 변경은 별도 명시 승인이 있을 때만 한다.
로컬 작업·설명·문서 초안만 요청받은 경우는 그 범위를 지킨다. 기존의 미커밋 작업을 묶어 원격 게시할 권한까지 추론하지 않는다.

원격 push나 PR 생성이 안 되면 완료라고 말하지 않는다. 어디까지 됐고 무엇이 막혔는지 보고한다.

## 12. 작업 끝난 뒤 정리

PR이 main에 merge된 뒤에만 정리한다. merge 전에는 브랜치를 지우지 않는다.

```bash
git switch main
git fetch origin
git merge --ff-only origin/main
```

다른 사람 브랜치는 지우지 않는다. 내 브랜치만 안전하게 지운다.

```bash
git branch -d feat/ai-policy-search
```

`-d`는 Git이 병합 여부를 확인하는 안전 삭제다. squash merge 후에는 원래 commit이 main의 조상이 아니어서 삭제가 거절될 수 있다. 그때는 브랜치를 보존하고 이유를 보고한다. `-D` 강제 삭제나 다른 사람의 원격 브랜치 삭제는 하지 않는다. 로컬 main의 `--ff-only` 갱신도 거절되면 강제로 reset하지 않는다.

## 13. 금지 목록

- main 직접 작업, 직접 push, main 외 장기 통합 브랜치
- codex, feature, 개인 이름 브랜치 접두어
- 다른 사람 미커밋 변경의 임의 commit, stash, reset
- merge 방향 뒤집기(작업 브랜치를 main에 직접 merge)
- 무조건 ours, theirs 충돌 해결과 검사 skip 통과
- 원격 동기화 없이 완료, 최신이라고 주장하기
- 자동 merge, 승인 대리, 배포, 보호 설정 변경을 임의로 하기

## 14. 자주 막히는 상황

| 상황 | 처리 |
|---|---|
| 브랜치가 이미 존재함 | 소유·작업 내용을 확인하고 해당 작업이면 재사용한다. 이름 충돌을 이유로 덮어쓰지 않는다. |
| fetch 실패·origin/main 없음 | 원격과 권한·네트워크를 확인한다. 원격 URL을 임의로 바꾸거나 최신이라고 주장하지 않는다. |
| push가 non-fast-forward로 거절됨 | 원격 작업 브랜치의 새 변경을 확인해 내 브랜치로 merge하고, main 동기화·검증 후 다시 push한다. force push하지 않는다. |
| CI 실패·필수 검사 미실행 | 실제 로그와 실패 원인을 수정한다. 로컬 성공만으로 통과라고 하지 않는다. |
| PR 후 main이 변경됨 | 작업 브랜치에서 다시 fetch·merge·검증·push한다. 리뷰에서 요청한 수정도 같은 PR에 반영한다. |
| 다른 작업의 미커밋 변경이 남음 | 임의로 포함·삭제하지 않고 소유자와 분리한다. 검사 우회를 위해 clean 확인을 끄지 않는다. |

PR을 올린 뒤에는 CI·리뷰 결과를 확인해 문제를 해결하고, 작성자 외 최소 1명의 리뷰가 끝나면 main 병합을 요청한다. 명시적 병합 승인 전에는 에이전트가 직접 병합하지 않는다.

관련 문서: [개발 시작](../README.md), [협업 규칙](CONTRIBUTING.md), [운영 설정](REPOSITORY_SETTINGS.md).
