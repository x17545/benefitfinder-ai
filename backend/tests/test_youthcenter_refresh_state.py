"""Failure checks for the F08 success pointer and run lock."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from benefitfinder.ingestion.collect import CollectionResult
from benefitfinder.jobs import youthcenter_refresh
from benefitfinder.jobs.youthcenter_refresh import RefreshError, run_youthcenter_refresh

AT = datetime(2026, 11, 7, tzinfo=timezone.utc)


def _complete(staging: Path, previous: Path | None) -> CollectionResult:
    (staging / "example.json").write_text("{}\n", encoding="utf-8")
    return CollectionResult("youthcenter", AT, (), (), (), True)


class RefreshStateTests(TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_status_replace_failure_does_not_select_the_uncommitted_generation(self) -> None:
        status = self.root / "status.json"
        self.assertTrue(run_youthcenter_refresh(self.root, _complete, lambda: AT))
        archived_id = json.loads(status.read_bytes())["last_success"]["run_id"]
        archived = self.root / "runs" / archived_id / "example.json"
        self.assertTrue(run_youthcenter_refresh(self.root, _complete, lambda: AT))
        old_status = status.read_bytes()
        old_id = json.loads(old_status)["last_success"]["run_id"]
        old_run = self.root / "runs" / old_id
        with (
            patch("benefitfinder.jobs.youthcenter_refresh.os.replace", side_effect=OSError),
            self.assertRaises(OSError),
        ):
            run_youthcenter_refresh(self.root, _complete, lambda: AT)
        self.assertEqual(status.read_bytes(), old_status)
        self.assertTrue((old_run / "example.json").is_file())
        self.assertFalse((self.root / ".refresh-lock").exists())
        self.assertEqual(archived.read_bytes(), b"{}\n")
        self.assertEqual(set((self.root / "runs").iterdir()), {old_run, archived.parent})

        next_build = Mock(side_effect=_complete)
        self.assertTrue(run_youthcenter_refresh(self.root, next_build, lambda: AT))
        self.assertEqual(next_build.call_args.args[1], old_run)

    def test_first_status_replace_failure_leaves_empty_runs(self) -> None:
        status = self.root / "status.json"
        with (
            patch("benefitfinder.jobs.youthcenter_refresh.os.replace", side_effect=OSError),
            self.assertRaises(OSError),
        ):
            run_youthcenter_refresh(self.root, _complete, lambda: AT)
        self.assertFalse(status.exists())
        self.assertEqual(list((self.root / "runs").iterdir()), [])
        self.assertFalse((self.root / ".refresh-lock").exists())
        self.assertTrue(run_youthcenter_refresh(self.root, _complete, lambda: AT))
        self.assertTrue(status.is_file())
        state = json.loads(status.read_text(encoding="utf-8"))
        new_id = state["last_success"]["run_id"]
        self.assertTrue((self.root / "runs" / new_id / "example.json").is_file())

    def test_post_commit_replace_error_preserves_selected_generation(self) -> None:
        status = self.root / "status.json"
        self.assertTrue(run_youthcenter_refresh(self.root, _complete, lambda: AT))
        old_id = json.loads(status.read_text(encoding="utf-8"))["last_success"]["run_id"]
        old_run = self.root / "runs" / old_id
        real_replace = os.replace

        def _replace_then_fail(src: Path, dst: Path) -> None:
            real_replace(src, dst)
            raise OSError("virtual post-commit failure")

        with (
            patch(
                "benefitfinder.jobs.youthcenter_refresh.os.replace",
                side_effect=_replace_then_fail,
            ),
            self.assertRaises(OSError),
        ):
            run_youthcenter_refresh(self.root, _complete, lambda: AT)
        new_id = json.loads(status.read_text(encoding="utf-8"))["last_success"]["run_id"]
        self.assertNotEqual(new_id, old_id)
        new_run = self.root / "runs" / new_id
        self.assertTrue((new_run / "example.json").is_file())
        self.assertTrue((old_run / "example.json").is_file())
        self.assertFalse((self.root / ".refresh-lock").exists())
        next_build = Mock(side_effect=_complete)
        self.assertTrue(run_youthcenter_refresh(self.root, next_build, lambda: AT))
        self.assertEqual(next_build.call_args.args[1], new_run)

    def test_unverifiable_status_after_failure_preserves_new_generation(self) -> None:
        status = self.root / "status.json"
        self.assertTrue(run_youthcenter_refresh(self.root, _complete, lambda: AT))
        old_status = status.read_bytes()
        old_id = json.loads(old_status)["last_success"]["run_id"]
        real_last = youthcenter_refresh._last_success
        calls: list[int] = []

        def _flaky_last(root: Path) -> tuple[dict[str, object] | None, Path | None]:
            calls.append(1)
            if len(calls) == 1:
                return real_last(root)
            raise OSError("virtual status re-read failure")

        with (
            patch("benefitfinder.jobs.youthcenter_refresh.os.replace", side_effect=OSError),
            patch(
                "benefitfinder.jobs.youthcenter_refresh._last_success",
                side_effect=_flaky_last,
            ),
            self.assertRaises(OSError),
        ):
            run_youthcenter_refresh(self.root, _complete, lambda: AT)
        self.assertEqual(status.read_bytes(), old_status)
        self.assertFalse((self.root / ".refresh-lock").exists())
        runs = sorted(p.name for p in (self.root / "runs").iterdir() if p.is_dir())
        self.assertIn(old_id, runs)
        self.assertEqual(len(runs), 2)
        fresh = [name for name in runs if name != old_id]
        self.assertEqual(len(fresh), 1)
        self.assertTrue((self.root / "runs" / fresh[0] / "example.json").is_file())

    def test_existing_lock_stops_before_collection(self) -> None:
        (self.root / ".refresh-lock").mkdir()
        build = Mock()
        with self.assertRaisesRegex(RefreshError, "^refresh_already_running$"):
            run_youthcenter_refresh(self.root, build, lambda: AT)
        build.assert_not_called()
        self.assertFalse((self.root / "status.json").exists())
