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
from benefitfinder.application.policy_facts import PolicyAmountCandidate, PolicyFactsDraft
from benefitfinder.application.policy_rules import ReviewIssue, RuleCitation
from benefitfinder.ingestion.rule_review_workflow import prepare_snapshot
from benefitfinder.infrastructure.policy_rule_files import (
    load_rule_batch,
    load_rule_snapshot,
    write_f12_review_pair,
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

    def test_extract_all_saves_both_pending_files_for_the_same_notice(self) -> None:
        candidates = example_rule_batch()
        (document,) = candidates.documents
        source = self.root / "input.json"
        output = self.root / "review"
        write_rule_batch(source, replace(candidates, candidates=()))
        original = source.read_bytes()
        facts = PolicyFactsDraft(document, (), (), ())
        args = (
            "extract-all",
            "--input",
            str(source),
            "--target-name",
            "가상 B",
            "--target-id",
            "youth:virtual:b",
            "--model",
            "gemma4:e4b",
            "--output-dir",
            str(output),
        )
        with (
            patch("benefitfinder.jobs.__main__.extract_rule_batch", return_value=(candidates, ())),
            patch("benefitfinder.jobs.__main__.extract_policy_facts", return_value=facts),
        ):
            status, _, stderr = self.run_command(*args)
        self.assertEqual(status, 0, stderr)
        rules = load_rule_snapshot(output / "rules-pending.json")
        saved_facts = json.loads((output / "facts-pending.json").read_text(encoding="utf-8"))
        self.assertEqual(rules.reviews[0].status, "needs_review")
        self.assertEqual(saved_facts["status"], "needs_review")
        self.assertEqual(
            saved_facts["source"]["content_sha256"], rules.batch.documents[0].content_sha256
        )
        self.assertEqual(source.read_bytes(), original)

        status, stdout, stderr = self.run_command("show-all", "--input-dir", str(output))
        self.assertEqual(status, 0, stderr)
        self.assertEqual(stdout.count("근거 후보 없음 (not_found)"), 3)
        self.assertIn("고정 월 지급일: 미확인", stdout)

        with patch("benefitfinder.jobs.__main__.OllamaChat") as chat:
            status, _, stderr = self.run_command(*args)
        self.assertEqual(status, 1)
        self.assertIn("rule_review_output_exists", stderr)
        chat.assert_not_called()

    def test_extract_all_does_not_save_rules_when_facts_extraction_fails(self) -> None:
        candidates = example_rule_batch()
        source = self.root / "input.json"
        output = self.root / "review"
        write_rule_batch(source, replace(candidates, candidates=()))
        with (
            patch("benefitfinder.jobs.__main__.extract_rule_batch", return_value=(candidates, ())),
            patch(
                "benefitfinder.jobs.__main__.extract_policy_facts",
                side_effect=ValueError("policy_facts_unverified_submission"),
            ),
        ):
            status, _, stderr = self.run_command(
                "extract-all",
                "--input",
                str(source),
                "--target-name",
                "가상 B",
                "--target-id",
                "youth:virtual:b",
                "--model",
                "gemma4:e4b",
                "--output-dir",
                str(output),
            )
        self.assertEqual(status, 1)
        self.assertIn("policy_facts_unverified_submission", stderr)
        self.assertFalse(output.exists())

    def _make_review_pair(self) -> Path:
        batch = example_rule_batch()
        (original,) = batch.documents
        (candidate,) = batch.candidates
        condition = "가상 수당 수혜 중에는 동시 참여 불가"
        exception = "가상 수당 수혜 종료 후 신청 가능"
        lines = (
            f"{condition}. 단, {exception}.",
            "가상 지역에 거주해야 합니다.",
            "지원 금액은 70,000원입니다.",
            "매월 17일 지급합니다.",
            "인용 범위 밖 가상 문장입니다.",
        )
        document = replace(original, text="\n".join(lines))
        rule, eligibility, amount, timing = (
            RuleCitation(
                document.id, document.document_version, document.content_sha256, line, f"line {i}"
            )
            for i, line in enumerate(lines[:4], 1)
        )
        candidate = replace(
            candidate,
            description=rule.quote,
            conditions=(condition,),
            exceptions=(exception,),
            citations=(rule,),
        )
        snapshot = prepare_snapshot(
            replace(batch, documents=(document,), candidates=(candidate,)), at=AT
        )
        facts = PolicyFactsDraft(
            document, (eligibility,), (PolicyAmountCandidate(amount, (70_000,)),), (timing,), 17
        )
        output = self.root / "review"
        write_f12_review_pair(output, snapshot, facts)
        return output

    def test_show_all_displays_both_files_and_citations_without_changing_them(self) -> None:
        output = self._make_review_pair()
        files = tuple(sorted(output.iterdir()))
        before = [path.read_bytes() for path in files]
        with patch("benefitfinder.jobs.__main__.OllamaChat") as chat:
            status, stdout, stderr = self.run_command("show-all", "--input-dir", str(output))
        self.assertEqual((status, stderr), (0, ""))
        chat.assert_not_called()
        self.assertEqual(tuple(sorted(output.iterdir())), files)
        self.assertEqual([path.read_bytes() for path in files], before)
        for expected in (
            "[중복 제한·예외]",
            "[자격 조건]",
            "[지원 금액]",
            "[지급 시점]",
            "needs_review",
            "제한 조건: 가상 수당 수혜 중에는 동시 참여 불가",
            "예외: 가상 수당 수혜 종료 후 신청 가능",
            "원문 근거 (line 1)",
            "가상 지역에 거주해야 합니다.",
            "명시 금액: 70,000원",
            "지원 금액은 70,000원입니다.",
            "고정 월 지급일: 매월 17일",
            "매월 17일 지급합니다.",
        ):
            self.assertIn(expected, stdout)
        self.assertNotIn("인용 범위 밖 가상 문장", stdout)

    def test_show_all_rejects_mismatched_source_or_unverified_quote_before_display(self) -> None:
        output = self._make_review_pair()
        facts = output / "facts-pending.json"
        original = facts.read_bytes()
        for bad_field in ("source hash", "citation quote", "missing file"):
            with self.subTest(bad_field=bad_field):
                payload = json.loads(original)
                if bad_field == "source hash":
                    payload["source"]["content_sha256"] = "a" * 64
                elif bad_field == "citation quote":
                    payload["eligibility"]["items"][0]["evidence"]["quote"] = (
                        "원문에 없는 가상 문구"
                    )
                if bad_field == "missing file":
                    facts.unlink()
                    error = "rule_review_io_error"
                else:
                    facts.write_text(json.dumps(payload), encoding="utf-8")
                    error = "invalid_rule_review_file"
                status, stdout, stderr = self.run_command("show-all", "--input-dir", str(output))
                self.assertEqual((status, stdout, stderr), (1, "", f"검수 작업 실패: {error}\n"))

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

    def test_refresh_cannot_restore_an_approval_invalid_in_the_stored_batch(self) -> None:
        source, snapshot, output = (self.root / name for name in ("input", "stale", "output"))
        original = approved()
        rival = replace(original.batch.candidates[0], id="virtual-rival")
        stale = replace(
            original,
            batch=replace(original.batch, candidates=(*original.batch.candidates, rival)),
        )
        write_rule_snapshot(snapshot, stale)
        write_rule_batch(source, original.batch)
        before = snapshot.read_bytes()

        status, stdout, _ = self.run_command(
            "refresh", "--snapshot", str(snapshot), "--input", str(source), "--output", str(output)
        )
        self.assertEqual(status, 0)
        self.assertIn("승인 0건", stdout)
        refreshed = load_rule_snapshot(output)
        self.assertIsNone(refreshed.reviews[0].approval)
        self.assertIn(
            "conflicting_candidates", {issue.code for issue in refreshed.reviews[0].issues}
        )
        self.assertEqual(snapshot.read_bytes(), before)

    def test_impossible_stored_review_time_is_rejected_without_writing(self) -> None:
        source, snapshot, output = (self.root / name for name in ("input", "bad-time", "output"))
        original = approved()
        later = replace(original.reviews[0].approval, reviewed_at=AT + timedelta(days=2))
        inconsistent = replace(original, reviews=(replace(original.reviews[0], approval=later),))
        write_rule_snapshot(snapshot, inconsistent)
        write_rule_batch(source, original.batch)
        before = snapshot.read_bytes()
        commands = (
            ("show", "--snapshot", str(snapshot)),
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
        )
        for arguments in commands:
            with self.subTest(command=arguments[0]):
                status, stdout, stderr = self.run_command(*arguments)
                self.assertEqual(status, 1)
                self.assertIn("review_time_follows_snapshot", stderr)
                self.assertNotIn(original.batch.documents[0].text, stdout + stderr)
                self.assertEqual(snapshot.read_bytes(), before)
                self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
