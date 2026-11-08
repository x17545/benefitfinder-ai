"""Failure boundaries for the two local F12 review files."""

import json
import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from benefitfinder.application.f12_review import F12ReviewDecision
from benefitfinder.application.policy_facts import PolicyAmountCandidate, PolicyFactsDraft
from benefitfinder.application.policy_rules import RuleCitation
from benefitfinder.infrastructure.policy_rule_files import (
    RuleReviewFileError,
    load_f12_decisions,
    load_f12_review_pair,
    write_f12_approved_export,
    write_f12_decisions,
    write_f12_review_pair,
)
from benefitfinder.ingestion.rule_review_workflow import prepare_snapshot
from benefitfinder.jobs.rule_review_example import example_rule_batch

_QUOTE_ELIGIBILITY = "가상 자격 거주 조건 문구"
_QUOTE_AMOUNT = "가상 지원 금액 70,000원 문구"
_QUOTE_TIMING = "가상 매월 17일 지급 문구"
_QUOTE_OUT_OF_SCOPE = "인용 범위 밖 가상 문장입니다."


def _facts_only_pair():
    """Return an empty-rules snapshot with qualification facts (virtual only)."""
    batch = example_rule_batch()
    (original,) = batch.documents
    text = "\n".join(
        [_QUOTE_ELIGIBILITY, _QUOTE_AMOUNT, _QUOTE_TIMING, _QUOTE_OUT_OF_SCOPE]
    )
    document = replace(
        original,
        id="virtual-notice-facts-only",
        source_url="https://example.invalid/virtual-facts-only",
        document_version="virtual-v1",
        text=text,
    )
    empty = replace(batch, documents=(document,), candidates=())
    snapshot = prepare_snapshot(empty, at=datetime(2026, 11, 6, tzinfo=timezone.utc))
    eligibility = RuleCitation(
        document.id, document.document_version, document.content_sha256,
        _QUOTE_ELIGIBILITY, "line 1",
    )
    amount = RuleCitation(
        document.id, document.document_version, document.content_sha256,
        _QUOTE_AMOUNT, "line 2",
    )
    timing = RuleCitation(
        document.id, document.document_version, document.content_sha256,
        _QUOTE_TIMING, "line 3",
    )
    draft = PolicyFactsDraft(
        document,
        (eligibility,),
        (PolicyAmountCandidate(amount, (70000,)),),
        (timing,),
        17,
    )
    return snapshot, draft


class F12ReviewPairTests(TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory = self.root / "pending"
        batch = example_rule_batch()
        self.snapshot = prepare_snapshot(batch, at=datetime(2026, 11, 6, tzinfo=timezone.utc))
        self.draft = PolicyFactsDraft(batch.documents[0], (), (), ())

    def test_failed_second_write_does_not_expose_rules(self) -> None:
        with (
            patch(
                "benefitfinder.infrastructure.policy_rule_files.write_policy_facts",
                side_effect=RuleReviewFileError("invalid_rule_review_file"),
            ),
            self.assertRaisesRegex(RuleReviewFileError, "invalid_rule_review_file"),
        ):
            write_f12_review_pair(self.directory, self.snapshot, self.draft)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_failed_second_link_removes_only_its_partial_result(self) -> None:
        link = os.link

        def fail_on_facts(source: Path, destination: Path) -> None:
            if Path(destination) == self.directory / "facts-pending.json":
                raise OSError("simulated link failure")
            link(source, destination)

        with (
            patch(
                "benefitfinder.infrastructure.policy_rule_files.os.link", side_effect=fail_on_facts
            ),
            self.assertRaisesRegex(RuleReviewFileError, "rule_review_io_error"),
        ):
            write_f12_review_pair(self.directory, self.snapshot, self.draft)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_empty_rules_with_facts_allows_decision_and_approved_export(self) -> None:
        snapshot, draft = _facts_only_pair()
        write_f12_review_pair(self.directory, snapshot, draft)
        loaded_snapshot, loaded_draft = load_f12_review_pair(self.directory)
        self.assertEqual(loaded_snapshot.reviews, ())
        self.assertEqual(len(loaded_draft.eligibility), 1)
        self.assertEqual(len(loaded_draft.amounts), 1)
        self.assertEqual(len(loaded_draft.payment_timing), 1)
        decision = F12ReviewDecision(
            "eligibility:1",
            "approve",
            "virtual-reviewer",
            "가상 자격 근거가 원문과 일치함",
            snapshot.created_at + timedelta(hours=1),
        )
        decisions_path = self.root / "decisions.json"
        write_f12_decisions(decisions_path, loaded_snapshot, loaded_draft, (decision,))
        self.assertEqual(
            len(load_f12_decisions(decisions_path, loaded_snapshot, loaded_draft)), 1
        )
        export_path = self.root / "export.json"
        self.assertEqual(
            write_f12_approved_export(
                export_path, loaded_snapshot, loaded_draft, decisions_path
            ),
            1,
        )
        payload = json.loads(export_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["policy_id"], draft.document.policy_id)
        self.assertEqual(
            payload["source"]["content_sha256"], draft.document.content_sha256
        )
        (item,) = payload["items"]
        self.assertEqual((item["item_id"], item["kind"]), ("eligibility:1", "eligibility"))
        self.assertEqual(item["candidate"]["evidence"]["quote"], _QUOTE_ELIGIBILITY)
        raw = export_path.read_text(encoding="utf-8")
        self.assertIn(_QUOTE_ELIGIBILITY, raw)
        self.assertNotIn(_QUOTE_OUT_OF_SCOPE, raw)
        self.assertNotIn("rule:1", raw)

    def test_empty_rules_without_facts_rejected_and_boundaries_kept(self) -> None:
        snapshot, full = _facts_only_pair()
        (document,) = snapshot.batch.documents
        empty_draft = PolicyFactsDraft(document, (), (), (), None)
        empty_dir = self.root / "empty"
        with self.assertRaisesRegex(RuleReviewFileError, "invalid_rule_review_file"):
            write_f12_review_pair(empty_dir, snapshot, empty_draft)
        self.assertFalse(empty_dir.exists())
        write_f12_review_pair(self.directory, snapshot, full)
        facts_path = self.directory / "facts-pending.json"
        payload = json.loads(facts_path.read_text(encoding="utf-8"))
        payload["eligibility"] = {"status": "not_found", "items": []}
        payload["benefit_amount"] = {"status": "not_found", "items": []}
        payload["payment_timing"] = {
            "status": "not_found",
            "items": [],
            "scheduled_monthly_day": None,
        }
        facts_path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(RuleReviewFileError, "invalid_rule_review_file"):
            load_f12_review_pair(self.directory)
        other = example_rule_batch().documents[0]
        mismatch_dir = self.root / "mismatch"
        mismatched = PolicyFactsDraft(
            other, full.eligibility, full.amounts, full.payment_timing,
            full.scheduled_monthly_day,
        )
        with self.assertRaisesRegex(RuleReviewFileError, "invalid_rule_review_file"):
            write_f12_review_pair(mismatch_dir, snapshot, mismatched)
        self.assertFalse(mismatch_dir.exists())
        unofficial_dir = self.root / "unofficial"
        write_f12_review_pair(unofficial_dir, snapshot, full)
        rules_path = unofficial_dir / "rules-pending.json"
        rules_payload = json.loads(rules_path.read_text(encoding="utf-8"))
        rules_payload["batch"]["documents"][0]["official"] = False
        rules_path.write_text(json.dumps(rules_payload), encoding="utf-8")
        with self.assertRaisesRegex(RuleReviewFileError, "invalid_rule_review_file"):
            load_f12_review_pair(unofficial_dir)
