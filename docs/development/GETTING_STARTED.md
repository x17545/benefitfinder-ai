# 처음 개발 시작하기

모든 명령은 저장소 루트(`benefitfinder-ai/`)의 터미널에서 실행한다. 웹과 API는 DB·AI 키 없이 실행할 수 있다. 웹은 시작·조건 입력·분석·결과 화면을 제공하며, 기본 백엔드는 생존 확인 API만 제공한다. 실제 정책 추천 API·DB·데이터 Agent는 후속 구현이다.

## 1. 도구 설치 (처음 한 번)

| 도구 | 역할 | 프로젝트 기준 |
|---|---|---|
| Node.js | Vite·React 개발 도구를 실행하는 런타임 | 24.x, `.node-version` |
| pnpm | 웹 패키지 설치와 개발 명령 실행 | 11.25.0, 루트 `package.json` |
| uv | Python 설치·가상환경·패키지·실행 관리 | 0.9.5 이상, Python 3.12 |

Python 의존성 선언은 루트 `pyproject.toml`, 웹은 루트와 `apps/web/package.json`에 있다. `uv.lock`·`pnpm-lock.yaml`은 팀원 모두가 같은 버전을 설치하는 잠금 파일이다. 공유하고 직접 편집·삭제하지 않는다. `pip`·`npm`·`npx`·yarn·Poetry를 섞지 않는다.

1. [Node.js 공식 다운로드](https://nodejs.org/en/download)에서 **24.x**를 설치한다. 이미 다른 버전을 쓰고 있다면 사용 중인 버전 관리 도구로 24를 선택한다.
2. [uv 공식 설치 안내](https://docs.astral.sh/uv/getting-started/installation/)를 따라 uv를 설치한다. Python은 다음 단계의 `uv sync`가 필요 시 3.12를 설치한다.
3. [pnpm 공식 설치 안내](https://pnpm.io/installation)의 독립 설치 스크립트를 사용해 프로젝트 버전을 설치한다. npm을 설치 경로로 사용하지 않는다.

macOS·Linux:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
curl -fsSL https://get.pnpm.io/install.sh | env PNPM_VERSION=11.25.0 sh -
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
$env:PNPM_VERSION = "11.25.0"
Invoke-WebRequest https://get.pnpm.io/install.ps1 -UseBasicParsing | Invoke-Expression
```

설치 후 터미널을 새로 열고 확인한다.

```sh
node --version   # v24.x
pnpm --version   # 11.25.0
uv --version    # 0.9.5 이상
```

저장소를 clone한 뒤 작업 브랜치를 만든다. 브랜치·PR 절차는 [Git 작업 절차](GIT_WORKFLOW.md)를 따른다. `main`에서 코드를 수정하지 않는다.

## 2. 의존성 설치

```sh
uv sync --locked
pnpm install --frozen-lockfile
```

uv가 루트 `.venv/`를 만들고 백엔드 패키지·개발 도구를 설치한다. pnpm은 `node_modules/`를 만든다. 둘 다 Git에는 올라가지 않는다. 가상환경을 직접 활성화할 필요 없이 `uv run`으로 실행한다.

새로 clone했거나 다른 팀원의 의존성 변경을 pull했을 때도 위 두 명령을 실행한다. lockfile이 선언 파일과 맞지 않으면 설치가 실패한다. lockfile을 지우지 말고 변경한 담당자와 확인한다.

## 3. 서버 실행

```sh
pnpm dev
```

| 주소 | 확인할 것 |
|---|---|
| http://127.0.0.1:5173 | 청년 정책 매니저 시작 화면, 필수 동의 후 13단계 조건 입력 |
| http://127.0.0.1:8000/health | `{"status":"ok"}` |
| http://127.0.0.1:8000/docs | FastAPI API 문서 |

두 서버를 함께 실행하며 `Ctrl+C`로 종료한다. 파일 변경은 Vite·Uvicorn이 반영한다. 각각 실행하려면 터미널 두 개에서 `pnpm dev:web`과 `pnpm dev:api`를 실행한다. API만 작업할 때는 아래 명령도 같다.

기본 백엔드에는 `/api/v1/options`·`/api/v1/recommendations`·정책 상세 API가 아직 없다. `pnpm dev`에서는 지역을 못 받으면 `[가상]` 목록을 표시한다. 분석 실패 화면의 [예제 결과로 보기]는 가상 계약으로 결과 화면을 확인하는 개발 전용 버튼이다. 이 흐름을 실제 정책 추천 성공으로 보고하지 않는다.

```sh
uv run --locked uvicorn benefitfinder.main:app --reload --reload-dir backend/src --host 127.0.0.1 --port 8000
```

기본 실행에 `.env` 복사는 필요 없다. 서버 주소를 바꾸면 `apps/web/.env.example`을 같은 폴더의 `.env`로 복사해 `API_PROXY_TARGET`을 수정하고 Vite를 다시 실행한다. `/api/v1`·`/health`는 개발 프록시를 사용한다. `backend/.env.example`은 향후 DB 설정 양식이고 현재 앱은 DB 환경 변수를 읽거나 접속하지 않는다. `/health`는 DB·데이터·LLM 준비 상태를 확인하지 않는다.

## 4. 매일 쓰는 검사

```sh
uv run --locked python scripts/check.py  # 저장소·계약·API·Ruff·웹 전체
pnpm check:web                          # 웹 생성 타입·lint·타입·테스트·빌드
uv run --locked python -m unittest discover -s backend/tests -v
```

PR 전에는 최신 main을 작업 브랜치에 반영하고 커밋한 뒤 `uv run --locked python scripts/check.py --pr`로 검사한다. 이 검사는 작업 트리가 깨끗해야 한다. 자세한 순서는 [Git 작업 절차](GIT_WORKFLOW.md)를 따른다.

## 5. 패키지나 계약을 바꿀 때

| 작업 | 명령 |
|---|---|
| Python 실행 의존성 추가 | `uv add 패키지이름` |
| Python 개발 도구 추가 | `uv add --dev 패키지이름` |
| 웹 실행 의존성 추가 | `pnpm --filter @benefitfinder/web add 패키지이름` |
| 웹 개발 도구 추가 | `pnpm --filter @benefitfinder/web add -D 패키지이름` |
| 공용 웹 도구 추가 | `pnpm add -Dw 패키지이름` |
| 설치된 도구 실행 | `uv run 도구이름` / `pnpm --filter @benefitfinder/web exec 도구이름` |
| 계약 JSON Schema 변경 후 | `pnpm types:generate` |

선언 파일과 해당 lockfile을 같은 PR에 넣는다. 생성 타입(`apps/web/src/shared/api/generated/`)도 공유하며 직접 수정하지 않는다. `pnpm types:check`와 CI가 오래된 타입을 찾는다. 새로운 의존성은 실제 필요와 담당 영역을 확인하고 추가한다.

## 6. 실행이 안 될 때

- `command not found`: 설치 후 터미널을 다시 열고 1장의 버전을 확인한다. 코딩 에이전트도 해당 도구의 PATH를 먼저 확인한다.
- pnpm의 의존성 불일치 오류: 루트에서 `pnpm install --frozen-lockfile`을 실행한다. 개발 명령이 자동으로 lockfile을 바꾸지 않도록 설정했다.
- 5173 또는 8000 포트 사용 중: 기존 개발 서버 터미널을 찾아 `Ctrl+C`로 종료한다. 다른 사람의 프로세스를 임의 종료하지 않는다. Vite는 몰래 다른 포트로 이동하지 않는다.
- 분석 실패 또는 지역 목록 오류: API 터미널과 `/health`를 확인한다. `/health` 성공은 정책 API 구현·준비를 뜻하지 않는다. 개발용 예제로 화면을 확인하는 방법은 3장을 따른다.
- 프록시 대상 변경: `.env`를 바꾼 뒤 Vite를 재시작한다. API 문서는 변경한 서버의 `/docs`로 직접 연다.
- 빌드 미리보기: `pnpm build` 후 `pnpm --filter @benefitfinder/web preview`를 쓴다. preview는 API 프록시·운영 배포를 제공하지 않으므로 API 연결 개발은 `pnpm dev`로 확인한다.
- 첫 설치의 다운로드 실패: 네트워크·공식 레지스트리 연결을 확인한다. 실패를 피하려고 잠금 파일·검사를 지우지 않는다.

설치·생성물 제외 정책은 [저장소 설정](REPOSITORY_SETTINGS.md), 영역별 설명은 [웹](../../apps/web/README.md)·[백엔드](../../backend/README.md)를 따른다.
