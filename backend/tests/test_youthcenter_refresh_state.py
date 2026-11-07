"""Failure checks for the F08 success pointer and run lock."""

import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from benefitfinder.ingestion.collect import CollectionResult
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

        next_build = Mock(side_effect=_complete)
        self.assertTrue(run_youthcenter_refresh(self.root, next_build, lambda: AT))
        self.assertEqual(next_build.call_args.args[1], old_run)

    def test_existing_lock_stops_before_collection(self) -> None:
        (self.root / ".refresh-lock").mkdir()
        build = Mock()
        with self.assertRaisesRegex(RefreshError, "^refresh_already_running$"):
            run_youthcenter_refresh(self.root, build, lambda: AT)
        build.assert_not_called()
        self.assertFalse((self.root / "status.json").exists())
