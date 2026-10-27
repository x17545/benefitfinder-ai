"""Exercise workflow checks using synthetic repositories, never the working tree."""

import io
import sys
from contextlib import redirect_stderr
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import scripts.check as check_module
from scripts.check import (
    backend_import_errors,
    branch_error,
    clean_tree_error,
    file_errors,
    git,
    main_sync_error,
    operation_errors,
)


class BranchTests(unittest.TestCase):
    def test_allowed_task_prefixes(self) -> None:
        for prefix in ("feat", "fix", "docs", "refactor", "test", "chore", "ci"):
            with self.subTest(prefix=prefix):
                self.assertIsNone(branch_error(f"{prefix}/ai-policy-search-2"))

    def test_prohibited_or_ambiguous_names(self) -> None:
        for branch in (
            "main", "codex/policy-search", "feature/policy-search", "person/task",
            "feat/", "feat/Uppercase", "feat/with_underscore", "feat/nested/task",
            "feat/-leading", "feat/trailing-", "feat/double--hyphen", "",
        ):
            with self.subTest(branch=branch):
                self.assertIsNotNone(branch_error(branch))
        self.assertIsNone(branch_error("main", allow_main=True))
        self.assertIsNotNone(branch_error("codex/task", allow_main=True))


class ImportBoundaryTests(unittest.TestCase):
    def test_pure_domain_relative_import_is_allowed(self) -> None:
        source = "from dataclasses import dataclass\nfrom .models import Benefit\n"
        self.assertEqual(
            backend_import_errors("backend/src/benefitfinder/domain/rules.py", source), []
        )

    def test_external_domain_and_cross_adapter_imports_are_rejected(self) -> None:
        cases = (
            ("domain/rules.py", "import requests\n"),
            ("ai/agent.py", "from benefitfinder import infrastructure\n"),
            ("ai/tools.py", "from ..ingestion import parse\n"),
            ("application/recommend.py", "from benefitfinder.ai import agent\n"),
            ("domains/youth/rules.py", "from benefitfinder.domains.other import rules\n"),
        )
        for relative, source in cases:
            with self.subTest(path=relative):
                self.assertTrue(backend_import_errors(f"backend/src/benefitfinder/{relative}", source))

    def test_same_domain_and_explicit_assembly_are_allowed(self) -> None:
        self.assertEqual(
            backend_import_errors(
                "backend/src/benefitfinder/domains/youth/rules.py",
                "from benefitfinder.domain import models\nfrom . import conditions\n",
            ), []
        )
        self.assertEqual(
            backend_import_errors(
                "backend/src/benefitfinder/bootstrap.py",
                "from benefitfinder.infrastructure import repository\n",
            ), []
        )


class GitWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="benefitfinder-workflow-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.command("init", "--initial-branch=main")
        self.command("config", "user.name", "Workflow Test")
        self.command("config", "user.email", "test@example.test")
        self.command("config", "commit.gpgsign", "false")
        hooks = self.root / "empty-hooks"
        hooks.mkdir()
        self.command("config", "core.hooksPath", str(hooks))
        self.command("commit", "--allow-empty", "-m", "test: initial")
        self.command("update-ref", "refs/remotes/origin/main", "HEAD")
        self.command("switch", "--no-track", "-c", "feat/workflow-test")

    def command(self, *args: str) -> str:
        result = git(self.root, *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def commit_file(self, name: str) -> None:
        (self.root / name).write_text("fictional test data\n", encoding="utf-8")
        self.command("add", name)
        self.command("commit", "-m", f"test: add {name}")

    def test_diverged_main_requires_merge_before_pr(self) -> None:
        self.commit_file("feature.txt")
        self.assertIsNone(main_sync_error(self.root))
        self.command("switch", "main")
        self.commit_file("main.txt")
        self.command("update-ref", "refs/remotes/origin/main", "HEAD")
        self.command("switch", "feat/workflow-test")
        self.assertIsNotNone(main_sync_error(self.root))
        self.command("merge", "--no-edit", "origin/main")
        self.assertIsNone(main_sync_error(self.root))
        self.assertIsNone(clean_tree_error(self.root))

    def test_missing_main_and_uncommitted_changes_are_reported(self) -> None:
        self.command("update-ref", "-d", "refs/remotes/origin/main")
        self.assertIsNotNone(main_sync_error(self.root))
        (self.root / "uncommitted.txt").write_text("fictional\n", encoding="utf-8")
        self.assertIsNotNone(clean_tree_error(self.root))

    def test_unfinished_git_operation_is_reported(self) -> None:
        state = self.root / ".git" / "MERGE_HEAD"
        state.write_text(self.command("rev-parse", "HEAD") + "\n", encoding="utf-8")
        self.assertTrue(operation_errors(self.root))
        state.unlink()
        self.assertEqual(operation_errors(self.root), [])

    def test_conflict_markers_and_sensitive_paths_are_reported(self) -> None:
        (self.root / "conflict.py").write_text(
            "<<<<<<< HEAD\nx = 1\n=======\nx = 2\n>>>>>>> main\n", encoding="utf-8"
        )
        (self.root / "conflict.md").write_text(
            "<<<<<<< HEAD\nfirst\n=======\nsecond\n>>>>>>> main\n", encoding="utf-8"
        )
        (self.root / ".env").write_text("EXAMPLE=fictional\n", encoding="utf-8")
        errors = file_errors(self.root)
        self.assertTrue(any("충돌 마커" in error for error in errors))
        self.assertTrue(any("conflict.md" in error and "충돌 마커" in error for error in errors))
        self.assertTrue(any(".env" in error for error in errors))
        self.assertFalse(any("EXAMPLE=fictional" in error for error in errors))

    def test_missing_required_test_file_fails_fast(self) -> None:
        required = (
            "tests/test_contracts.py",
            "tests/test_gitignore.py",
            "tests/test_workflow.py",
            "tests/test_planning_rules.py",
        )
        (self.root / "tests").mkdir(exist_ok=True)
        for name in required:
            (self.root / name).write_text("# fictional\n", encoding="utf-8")
        for missing in required:
            with self.subTest(missing=missing):
                (self.root / missing).unlink()
                with mock.patch.object(check_module, "ROOT", self.root), mock.patch.object(
                    sys, "argv", ["check.py"]
                ), redirect_stderr(io.StringIO()) as err:
                    result = check_module.main()
                self.assertEqual(result, 1)
                self.assertIn(missing, err.getvalue())
                (self.root / missing).write_text("# fictional\n", encoding="utf-8")

    def test_extended_text_suffixes_and_diff3_marker_are_reported(self) -> None:
        cases = (
            ("page.html", b"<!DOCTYPE html>\r\n<html></html>\n"),
            ("style.css", b"a {\r\n  color: red;\n}\n"),
            ("run.sh", b"#!/bin/sh\r\necho fictional\n"),
            ("Dockerfile", b"FROM fictional\r\n"),
            (".env.example", b"VITE_API_BASE_URL=/api/v1\r\n"),
        )
        for name, raw in cases:
            with self.subTest(path=name):
                target = self.root / name
                target.write_bytes(raw)
                errors = file_errors(self.root)
                self.assertTrue(
                    any(name in error and "LF" in error for error in errors),
                    msg="\n".join(errors),
                )
                target.unlink()
        with self.subTest(path="diff3.txt"):
            target = self.root / "diff3.txt"
            target.write_text(
                "first\n||||||| merged common ancestor\nsecond\n", encoding="utf-8"
            )
            errors = file_errors(self.root)
            self.assertTrue(
                any("diff3.txt" in error and "충돌 마커" in error for error in errors),
                msg="\n".join(errors),
            )

    def test_mixed_package_manager_lockfiles_are_rejected(self) -> None:
        for name in ("package-lock.json", "yarn.lock", "poetry.lock", "Pipfile.lock"):
            with self.subTest(path=name):
                target = self.root / name
                target.write_text("{}\n", encoding="utf-8")
                self.assertTrue(any(name in error and "잠금 파일" in error for error in file_errors(self.root)))
                target.unlink()

    def test_allow_main_rejects_non_main_branch(self) -> None:
        required = (
            "tests/test_contracts.py",
            "tests/test_gitignore.py",
            "tests/test_workflow.py",
            "tests/test_planning_rules.py",
        )
        (self.root / "tests").mkdir(exist_ok=True)
        for name in required:
            (self.root / name).write_text("# fictional\n", encoding="utf-8")
        with mock.patch.object(check_module, "ROOT", self.root), mock.patch.object(
            sys, "argv", ["check.py", "--allow-main"]
        ), mock.patch.dict(
            check_module.os.environ, {"GITHUB_ACTIONS": "true"}
        ), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                check_module.main()
        self.assertNotEqual(ctx.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
