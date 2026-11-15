"""Recheck handoff is create-only, private and prints aggregates only."""

from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from benefitfinder.jobs.__main__ import main


class PolicyRecheckJobsTest(unittest.TestCase):
    def test_exports_private_page_without_overwriting_or_marking_done(self) -> None:
        page = {"format_version": 1, "kind": "policy_recheck_page",
                "items": [{"reviewed_by": "fictional-person-not-for-console"}], "next_after_id": 7}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "rechecks.json"
            for code in (0, 2):
                stdout, stderr = StringIO(), StringIO()
                with patch("benefitfinder.jobs.__main__.list_policy_rechecks", return_value=page) as read, patch.dict("os.environ", {"POLICY_RECHECK_DATABASE_URL": "fictional-private-url"}), redirect_stdout(stdout), redirect_stderr(stderr):
                    self.assertEqual(main(["policy-catalog", "rechecks", "--output", str(output),
                                           "--after-id", "3", "--limit", "2"]), code)
                read.assert_called_once_with("fictional-private-url", after_id=3, limit=2)
                self.assertNotIn("fictional-person", stdout.getvalue() + stderr.getvalue())
                self.assertNotIn("fictional-private-url", stdout.getvalue() + stderr.getvalue())
            self.assertEqual(json.loads(output.read_text()), page)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(Path(directory).glob(".rechecks-*")), [])

    def test_failure_leaves_no_output_and_hides_driver_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "rechecks.json"
            stdout, stderr = StringIO(), StringIO()
            with patch("benefitfinder.jobs.__main__.list_policy_rechecks", side_effect=RuntimeError("secret-driver-body")), redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(main(["policy-catalog", "rechecks", "--output", str(output)]), 2)
            self.assertFalse(output.exists())
            self.assertNotIn("secret-driver-body", stdout.getvalue() + stderr.getvalue())

    def test_existing_symlink_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "existing.json"
            target.write_text("keep-prior-review")
            output = Path(directory) / "new.json"
            output.symlink_to(target)
            with patch("benefitfinder.jobs.__main__.list_policy_rechecks", return_value={"items": [], "next_after_id": None}), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                self.assertEqual(main(["policy-catalog", "rechecks", "--output", str(output)]), 2)
            self.assertTrue(output.is_symlink())
            self.assertEqual(target.read_text(), "keep-prior-review")
