"""Verify .gitignore keeps secrets and generated files out, never the working tree."""

from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.check import file_errors


REPO_ROOT = Path(__file__).resolve().parents[1]

IGNORED_PATHS = (
    ".env",
    ".env.local",
    "backend/.env.production",
    "apps/web/.env.development.local",
    "backend/production.env",
    "secrets/token.json",
    ".secrets/key.json",
    "backend/service-account-dev.json",
    "private.pem",
    "private.key",
    "credentials.json",
    ".npmrc",
    ".pgpass",
    "backend/.venv/bin/python",
    "venv/bin/python",
    "__pycache__/module.pyc",
    ".mypy_cache/state.json",
    ".ruff_cache/cache",
    "apps/web/node_modules/react/index.js",
    "apps/web/dist/index.html",
    "apps/web/.vite/cache.json",
    "apps/web/app.tsbuildinfo",
    "coverage/index.html",
    ".coverage.local",
    "playwright-report/index.html",
    "test-results/report.xml",
    "data/raw/policy.json",
    "data/processed/policy.csv",
    "data/indexes/index.faiss",
    "data/random-export.json",
    "backend/data/raw/policy.json",
    "backups/prod.sql",
    "backend/dumps/prod.sql",
    "pgdata/PG_VERSION",
    "backend/local.dump",
    "benefitfinder.sql.gz",
    "production.sqlite3-wal",
    "app.log.1",
    ".idea/workspace.xml",
    ".vscode/settings.json",
    ".DS_Store",
    "editor.swp",
)

ALLOWED_PATHS = (
    ".gitignore",
    "AGENTS.md",
    "CLAUDE.md",
    ".cursor/rules/project.mdc",
    ".github/workflows/ci.yml",
    ".editorconfig",
    "README.md",
    "data/README.md",
    ".env.example",
    "backend/.env.example",
    "apps/web/.env.example",
    "apps/web/package.json",
    "apps/web/package-lock.json",
    "apps/web/tsconfig.json",
    "apps/web/vite.config.ts",
    "apps/web/src/vite-env.d.ts",
    "apps/web/public/.gitkeep",
    "backend/migrations/versions/0001.sql",
    "backend/migrations/versions/0001.py",
    "contracts/v1/benefit.schema.json",
    "contracts/fixtures/policy.json",
    "tests/fixtures/policy.csv",
    "tests/fixtures/query.sql",
    "backend/src/benefitfinder/infrastructure/database/__init__.py",
)

FORCED_DUMMY_FILES = (
    ("backups/prod.sql", "SELECT 1;\n"),
    ("apps/web/dist/index.html", "<html></html>\n"),
    ("backend/.env", "DUMMY_KEY=dummy-value\n"),
)


def run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


class GitignoreTests(unittest.TestCase):
    def make_isolated_repo(self) -> Path:
        temporary = tempfile.TemporaryDirectory(prefix="benefitfinder-gitignore-")
        self.addCleanup(temporary.cleanup)
        repo = Path(temporary.name)
        result = run_git(repo, "init", "--initial-branch=main")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        result = run_git(repo, "config", "user.name", "Gitignore Test")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        result = run_git(repo, "config", "user.email", "test@example.test")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        (repo / ".gitignore").write_bytes((REPO_ROOT / ".gitignore").read_bytes())
        excludes = repo / "empty-excludes"
        excludes.write_text("", encoding="utf-8")
        result = run_git(repo, "config", "core.excludesFile", str(excludes))
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        hooks = repo / "empty-hooks"
        hooks.mkdir()
        result = run_git(repo, "config", "core.hooksPath", str(hooks))
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        return repo

    def check_ignore_code(self, repo: Path, relative: str) -> int:
        target = repo / relative
        if target.parent != repo:
            target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_text("", encoding="utf-8")
        result = run_git(repo, "check-ignore", "--", relative)
        self.assertIn(
            result.returncode,
            (0, 1),
            msg=f"{relative}: git check-ignore failed: {result.stderr}",
        )
        return result.returncode

    def test_ignored_paths_stay_ignored(self) -> None:
        repo = self.make_isolated_repo()
        for relative in IGNORED_PATHS:
            with self.subTest(path=relative):
                self.assertEqual(self.check_ignore_code(repo, relative), 0)

    def test_allowed_paths_stay_trackable(self) -> None:
        repo = self.make_isolated_repo()
        for relative in ALLOWED_PATHS:
            with self.subTest(path=relative):
                self.assertEqual(self.check_ignore_code(repo, relative), 1)

    def test_forced_added_ignored_files_are_reported_without_content(self) -> None:
        repo = self.make_isolated_repo()
        for relative, content in FORCED_DUMMY_FILES:
            with self.subTest(path=relative):
                target = repo / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                result = run_git(repo, "add", "-f", "--", relative)
                self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertNotEqual(run_git(repo, "rev-parse", "--verify", "HEAD").returncode, 0)
        errors = file_errors(repo)
        joined = "\n".join(errors)
        for relative, content in FORCED_DUMMY_FILES:
            with self.subTest(path=relative):
                self.assertIn(relative, joined)
                self.assertNotIn(content.strip(), joined)

    def test_global_only_ignored_file_is_not_flagged(self) -> None:
        repo = self.make_isolated_repo()
        global_excludes = repo / "global-excludes"
        global_excludes.write_text("README.md\n", encoding="utf-8")
        result = run_git(repo, "config", "core.excludesFile", str(global_excludes))
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        target = repo / "README.md"
        target.write_text("# dummy\n", encoding="utf-8")
        result = run_git(repo, "add", "-f", "--", "README.md")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        joined = "\n".join(file_errors(repo))
        self.assertNotIn("README.md", joined)


if __name__ == "__main__":
    unittest.main()
