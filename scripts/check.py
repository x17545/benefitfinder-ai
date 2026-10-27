"""Run local checks without changing branches, fetching, staging, or committing."""

import argparse
import ast
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
PREFIXES = ("feat", "fix", "docs", "refactor", "test", "chore", "ci")
BRANCH_PATTERN = re.compile(
    rf"(?:{'|'.join(PREFIXES)})/[a-z0-9]+(?:-[a-z0-9]+)*"
)
TEXT_SUFFIXES = {
    ".py", ".md", ".mdc", ".json", ".yml", ".yaml", ".toml", ".txt",
    ".html", ".css", ".scss", ".sh", ".bash", ".zsh",
}
TEXT_FILENAMES = {
    ".gitignore", ".editorconfig", ".env.example", "Dockerfile", "Containerfile", "Makefile",
}
CODE_SUFFIXES = {".py", ".json", ".yml", ".yaml", ".toml", ".js", ".jsx", ".ts", ".tsx"}
CONFLICT_MARKER = re.compile(r"^(?:<{7}(?: .*)?|\|{7}(?: .*)?|={7}|>{7}(?: .*)?)$", re.MULTILINE)
REQUIRED_TEST_FILES = (
    "tests/test_contracts.py", "tests/test_gitignore.py", "tests/test_workflow.py",
    "tests/test_planning_rules.py",
)
BACKEND_PREFIX = "backend/src/benefitfinder/"
ASSEMBLY_FILES = {"bootstrap.py", "main.py", "__main__.py", "jobs/__main__.py"}
ALLOWED_LAYERS = {
    "domain": {"domain"},
    "application": {"application", "domain"},
    "api": {"api", "application", "domain"},
    "ai": {"ai", "application", "domain", "domains"},
    "ingestion": {"ingestion", "application", "domain", "domains"},
    "infrastructure": {"infrastructure", "application", "domain", "domains"},
    "jobs": {"jobs", "application", "ingestion", "domain"},
}


def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=False
    )


def branch_error(branch: str, allow_main: bool = False) -> str | None:
    if branch == "main" and allow_main:
        return None
    if BRANCH_PATTERN.fullmatch(branch):
        return None
    return (
        f"허용되지 않은 브랜치: {branch or '(detached HEAD)'}. "
        f"{', '.join(prefix + '/' for prefix in PREFIXES)}와 소문자·숫자·하이픈을 사용하세요."
    )


def operation_errors(root: Path) -> list[str]:
    errors = []
    for state in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply"):
        result = git(root, "rev-parse", "--git-path", state)
        state_path = Path(result.stdout.strip())
        if result.returncode == 0 and state_path != Path("."):
            if not state_path.is_absolute():
                state_path = root / state_path
            if state_path.exists():
                errors.append(f"미완료 Git 작업({state})을 먼저 해결하세요.")
    conflicts = git(root, "diff", "--name-only", "--diff-filter=U")
    if conflicts.returncode != 0:
        errors.append("충돌 상태를 읽지 못했습니다.")
    elif conflicts.stdout.strip():
        errors.append("아직 해결되지 않은 충돌 파일이 있습니다.")
    return errors


def main_sync_error(root: Path) -> str | None:
    base = "refs/remotes/origin/main"
    if git(root, "rev-parse", "--verify", f"{base}^{{commit}}").returncode:
        return "origin/main이 없습니다. 네트워크 상태를 확인하고 git fetch origin을 실행하세요."
    result = git(root, "merge-base", "--is-ancestor", base, "HEAD")
    if result.returncode == 0:
        return None
    if result.returncode == 1:
        return "최신 확인한 origin/main을 작업 브랜치에 머지하고 충돌 해결·검증을 다시 하세요."
    return "main과 작업 브랜치의 관계를 확인하지 못했습니다. Git 이력 상태를 확인하세요."


def clean_tree_error(root: Path) -> str | None:
    result = git(root, "status", "--porcelain", "--untracked-files=normal")
    if result.returncode:
        return "작업 트리 상태를 읽지 못했습니다."
    if result.stdout.strip():
        return "PR 준비에는 깨끗한 작업 트리가 필요합니다. 소유가 확인된 변경만 선택 커밋하세요."
    return None


def backend_import_errors(path: str, source: str) -> list[str]:
    if not path.startswith(BACKEND_PREFIX):
        return []
    relative = path.removeprefix(BACKEND_PREFIX)
    if relative in ASSEMBLY_FILES or relative == "__init__.py":
        return []
    parts = Path(relative).parts
    owner = parts[0]
    if owner not in ALLOWED_LAYERS and owner != "domains":
        return [f"{path}: 새 공통 모듈의 위치·의존 방향을 먼저 설계에 반영하세요."]
    module_parts = list(Path(relative).with_suffix("").parts)
    package_parts = module_parts[:-1]
    package = ".".join(["benefitfinder", *package_parts])
    errors = []
    for node in ast.walk(ast.parse(source, filename=path)):
        targets = []
        if isinstance(node, ast.Import):
            targets = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            name = "." * node.level + (node.module or "")
            try:
                base = importlib.util.resolve_name(name, package) if node.level else name
            except (ImportError, ValueError):
                errors.append(f"{path}:{node.lineno}: 올바르지 않은 상대 import입니다.")
                continue
            targets = [base]
            if base == "benefitfinder" or base.startswith("benefitfinder."):
                targets.extend(f"{base}.{alias.name}" for alias in node.names)
        for target in targets:
            if target == "benefitfinder":
                continue
            if target.startswith("benefitfinder."):
                target_parts = target.split(".")
                layer = target_parts[1]
                if owner == "domains":
                    allowed = layer == "domain" or (
                        layer == "domains"
                        and (len(target_parts) == 2 or len(parts) > 1
                             and target_parts[2] == parts[1])
                    )
                else:
                    allowed = layer in ALLOWED_LAYERS[owner]
                if not allowed:
                    errors.append(f"{path}:{node.lineno}: {owner} → {target} 의존 금지.")
            elif owner in {"domain", "domains"}:
                if target.split(".")[0] not in sys.stdlib_module_names:
                    errors.append(f"{path}:{node.lineno}: 순수 업무 모듈의 외부 import 금지: {target}")
    return sorted(set(errors))


def file_errors(root: Path) -> list[str]:
    result = git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    if result.returncode:
        return ["검사할 파일 목록을 읽지 못했습니다."]
    ignored_result = git(
        root, "ls-files", "--cached", "--ignored", "--exclude-per-directory=.gitignore", "-z"
    )
    if ignored_result.returncode:
        return [".gitignore 제외 파일의 추적 상태를 읽지 못했습니다."]
    ignored_tracked = set(filter(None, ignored_result.stdout.split("\0")))
    errors = [
        f"{name}: .gitignore 제외 파일이 추적 중입니다. 강제 추가하지 말고 포함 경위를 확인하세요."
        for name in sorted(ignored_tracked)
    ]
    for name in sorted(set(filter(None, result.stdout.split("\0")))):
        if name in ignored_tracked:
            continue
        path = root / name
        if not path.is_file():
            continue
        filename = path.name
        if (filename == ".env" or filename.startswith(".env.") and filename != ".env.example"
                or path.suffix in {".key", ".pem"}
                or name.startswith(("data/raw/", "data/processed/", "data/indexes/"))):
            errors.append(f"{name}: 실제 데이터·자격 증명 경로는 Git에 포함하지 마세요.")
            continue
        if path.suffix not in TEXT_SUFFIXES | CODE_SUFFIXES and filename not in TEXT_FILENAMES:
            continue
        try:
            raw = path.read_bytes()
            source = raw.decode("utf-8")
        except (OSError, UnicodeError):
            errors.append(f"{name}: UTF-8 텍스트를 읽지 못했습니다.")
            continue
        if b"\r" in raw or raw and not raw.endswith(b"\n"):
            errors.append(f"{name}: LF 줄바꿈과 파일 끝 개행을 사용하세요.")
        if any(line != line.rstrip() for line in source.splitlines()):
            errors.append(f"{name}: 줄 끝 공백을 제거하세요.")
        if CONFLICT_MARKER.search(source):
            errors.append(f"{name}: 미해결 충돌 마커가 있습니다.")
        try:
            if path.suffix == ".json":
                json.loads(source)
            if path.suffix == ".py":
                ast.parse(source, filename=name)
                errors.extend(backend_import_errors(name, source))
        except (SyntaxError, ValueError):
            errors.append(f"{name}: 문법 오류를 해결하세요.")
        if path.suffix == ".md":
            for target in re.findall(r"\]\(([^)]+)\)", source):
                if "://" in target or target.startswith(("#", "mailto:")):
                    continue
                if not (path.parent / target.split("#", 1)[0]).exists():
                    errors.append(f"{name}: 로컬 문서 링크가 없습니다: {target}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr", action="store_true", help="main 포함 여부와 clean 상태도 검사")
    parser.add_argument("--branch", help="detached HEAD인 CI에서 실제 작업 브랜치명 지정")
    parser.add_argument("--allow-main", action="store_true", help="main의 CI 검사 전용")
    args = parser.parse_args()
    if args.pr and args.allow_main:
        parser.error("--pr은 --allow-main과 함께 사용할 수 없습니다.")
    if args.allow_main and os.environ.get("GITHUB_ACTIONS") != "true":
        parser.error("--allow-main은 main의 GitHub CI 검사 전용입니다.")
    result = git(ROOT, "branch", "--show-current")
    if result.returncode:
        print("Git 저장소의 상태를 읽지 못했습니다.", file=sys.stderr)
        return 1
    actual_branch = result.stdout.strip()
    branch = args.branch if args.branch is not None else actual_branch
    if args.allow_main and branch != "main":
        parser.error("--allow-main은 main의 GitHub CI 검사에만 사용할 수 있습니다.")
    errors = operation_errors(ROOT) + file_errors(ROOT)
    if actual_branch and branch != actual_branch:
        errors.append("--branch가 실제 브랜치와 다릅니다. CI 옵션으로 로컬 브랜치를 숨기지 마세요.")
    for required_test in REQUIRED_TEST_FILES:
        if not (ROOT / required_test).is_file():
            errors.append(f"필수 회귀 테스트({required_test})가 없습니다.")
    error = branch_error(branch, args.allow_main)
    if error:
        errors.append(error)
    if args.pr:
        errors.extend(error for error in (main_sync_error(ROOT), clean_tree_error(ROOT)) if error)
    if errors:
        print("\n".join(f"ERROR: {error}" for error in errors), file=sys.stderr)
        return 1
    for test_path in ("tests", "backend/tests"):
        if not any((ROOT / test_path).rglob("test_*.py")):
            continue
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", test_path, "-v"],
            cwd=ROOT, check=False,
        )
        if result.returncode:
            return result.returncode
    if args.pr and (error := clean_tree_error(ROOT)):
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("공통 검사 통과: Git·파일·직접 import·계약·기획용 가상 정답·발견된 Python 테스트")
    print("미구성: 프론트엔드 실행 검사·실제 API/LLM 통합·정책 사실 검증")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
