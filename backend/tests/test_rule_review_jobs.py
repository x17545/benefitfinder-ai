"""Local review commands tested with synthetic artifacts and temporary directories."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from datetime import timedelta
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from backend.tests.test_rule_review_workflow import AT, approved, prepared
from benefitfinder.application.policy_rules import ReviewIssue
from benefitfinder.infrastructure.policy_rule_files import (
    load_rule_batch,
    load_rule_snapshot,
    write_rule_batch,
    write_rule_snapshot,
)
from benefitfinder.jobs.__main__ import main
from benefitfinder.jobs.rule_review_example import example_rule_batch


class ReviewCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def run_command(self, *arguments: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            with patch("benefitfinder.jobs.__main__.datetime") as review_clock:
                review_clock.now.return_value = AT + timedelta(days=1)
                status = main(["rule-review", *arguments])
        return status, stdout.getvalue(), stderr.getvalue()

    def test_example_prepare_approve_show_and_refresh_use_new_files(self) -> None:
        source = self.root / "input.json"
        pending = self.root / "pending.json"
        signed = self.root / "signed.json"
        current = self.root / "current.json"
        rechecked = self.root / "rechecked.json"
        status, stdout, _ = self.run_command("example", "--output", str(source))
        self.assertEqual(status, 0)
        self.assertIn("[가상]", stdout)
        original_input = source.read_bytes()
        self.assertEqual(
            self.run_command(
                "prepare",
                "--input",
                str(source),
                "--output",
                str(pending),
            )[0],
            0,
        )
        original_pending = pending.read_bytes()
        self.assertEqual(
            self.run_command(
                "approve",
                "--snapshot",
                str(pending),
                "--candidate-id",
                "virtual-a-b",
                "--reviewer",
                "virtual-reviewer",
                "--output",
                str(signed),
            )[0],
            0,
        )
        original_signed = signed.read_bytes()
        status, stdout, _ = self.run_command("show", "--snapshot", str(signed))
        self.assertEqual(status, 0)
        self.assertIn("승인 1건", stdout)
        self.assertNotIn(example_rule_batch().documents[0].text, stdout)
        batch = replace(load_rule_batch(source), documents=())
        write_rule_batch(current, batch)
        self.assertEqual(
            self.run_command(
                "refresh",
                "--snapshot",
                str(signed),
                "--input",
                str(current),
                "--output",
                str(rechecked),
            )[0],
            0,
        )
        self.assertEqual(load_rule_snapshot(rechecked).reviews[0].status, "needs_review")
        self.assertEqual(source.read_bytes(), original_input)
        self.assertEqual(pending.read_bytes(), original_pending)
        self.assertEqual(signed.read_bytes(), original_signed)

    def test_existing_output_is_never_overwritten(self) -> None:
        path = self.root / "existing.json"
        path.write_bytes(b"previous-success")
        status, _, stderr = self.run_command("example", "--output", str(path))
        self.assertEqual(status, 1)
        self.assertIn("rule_review_output_exists", stderr)
        self.assertEqual(path.read_bytes(), b"previous-success")

    def test_snapshot_commands_cannot_replace_their_input_files(self) -> None:
        source, pending, signed = (self.root / name for name in ("input", "pending", "signed"))
        write_rule_batch(source, example_rule_batch())
        write_rule_snapshot(pending, prepared())
        write_rule_snapshot(signed, approved())
        commands = (
            (source, ("prepare", "--input", str(source), "--output", str(source))),
            (
                pending,
                (
                    "approve",
                    "--snapshot",
                    str(pending),
                    "--candidate-id",
                    "virtual-a-b",
                    "--reviewer",
                    "virtual-reviewer",
                    "--output",
                    str(pending),
                ),
            ),
            (
                signed,
                (
                    "refresh",
                    "--snapshot",
                    str(signed),
                    "--input",
                    str(source),
                    "--output",
                    str(signed),
                ),
            ),
        )
        for original, arguments in commands:
            with self.subTest(command=arguments[0]):
                before = original.read_bytes()
                status, _, stderr = self.run_command(*arguments)
                self.assertEqual(status, 1)
                self.assertIn("rule_review_output_exists", stderr)
                self.assertEqual(original.read_bytes(), before)

    def test_malformed_input_does_not_expose_content_or_write_output(self) -> None:
        source, output = self.root / "bad.json", self.root / "result.json"
        secret = "virtual-private-value-do-not-print"
        source.write_text(json.dumps({"format_version": 1, "profile": secret}), encoding="utf-8")
        status, stdout, stderr = self.run_command(
            "prepare",
            "--input",
            str(source),
            "--output",
            str(output),
        )
        self.assertEqual(status, 1)
        self.assertNotIn(secret, stdout + stderr)
        self.assertFalse(output.exists())

    def test_pending_approval_failure_keeps_previous_result_unchanged(self) -> None:
        snapshot, output = self.root / "pending.json", self.root / "approved.json"
        original = prepared()
        held = replace(original.reviews[0], issues=(ReviewIssue("manual_source_conflict"),))
        write_rule_snapshot(snapshot, replace(original, reviews=(held,)))
        before = snapshot.read_bytes()
        status, _, stderr = self.run_command(
            "approve",
            "--snapshot",
            str(snapshot),
            "--candidate-id",
            "virtual-a-b",
            "--reviewer",
            "virtual-reviewer",
            "--output",
            str(output),
        )
        self.assertEqual(status, 1)
        self.assertIn("candidate_not_approvable", stderr)
        self.assertEqual(snapshot.read_bytes(), before)
        self.assertFalse(output.exists())

    def test_corrupt_snapshot_is_not_echoed_by_approve_refresh_or_show(self) -> None:
        snapshot, source, output = (self.root / name for name in ("bad", "input", "output"))
        secret = "virtual-private-snapshot-content"
        snapshot.write_text(json.dumps({"format_version": 1, "private": secret}), encoding="utf-8")
        write_rule_batch(source, example_rule_batch())
        commands = (
            (
                "approve",
                "--snapshot",
                str(snapshot),
                "--candidate-id",
                "virtual-a-b",
                "--reviewer",
                "virtual-reviewer",
                "--output",
                str(output),
            ),
            (
                "refresh",
                "--snapshot",
                str(snapshot),
                "--input",
                str(source),
                "--output",
                str(output),
            ),
            ("show", "--snapshot", str(snapshot)),
        )
        for arguments in commands:
            with self.subTest(command=arguments[0]):
                status, stdout, stderr = self.run_command(*arguments)
                self.assertEqual(status, 1)
                self.assertNotIn(secret, stdout + stderr)
                self.assertFalse(output.exists())

    def test_show_revalidates_hashes_without_modifying_the_file(self) -> None:
        snapshot = self.root / "signed.json"
        original = approved()
        tampered = replace(original.reviews[0].candidate, description="[가상] 다른 규칙")
        altered = replace(original.reviews[0], candidate=tampered)
        batch = replace(original.batch, candidates=(tampered,))
        write_rule_snapshot(snapshot, replace(original, batch=batch, reviews=(altered,)))
        before = snapshot.read_bytes()
        status, stdout, _ = self.run_command("show", "--snapshot", str(snapshot))
        self.assertEqual(status, 0)
        self.assertIn("승인 0건", stdout)
        self.assertIn("검수 대기 1건", stdout)
        self.assertEqual(snapshot.read_bytes(), before)

    def test_io_error_messages_are_not_echoed(self) -> None:
        secret = "virtual-io-secret"
        with patch(
            "benefitfinder.jobs.__main__.write_rule_batch",
            side_effect=OSError(secret),
        ):
            status, stdout, stderr = self.run_command("example", "--output", str(self.root / "x"))
        self.assertEqual(status, 1)
        self.assertIn("rule_review_io_error", stderr)
        self.assertNotIn(secret, stdout + stderr)


if __name__ == "__main__":
    unittest.main()
