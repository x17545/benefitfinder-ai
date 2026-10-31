"""Offline review operations and commands using synthetic policy documents only."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest

from benefitfinder.application.policy_rules import ReviewIssue, ReviewOperation, RuleReviewSnapshot
from benefitfinder.ingestion.rule_review_workflow import (
    RuleReviewWorkflowError,
    approve_snapshot,
    inspect_snapshot,
    prepare_snapshot,
    refresh_snapshot,
)
from benefitfinder.jobs.rule_review_example import example_rule_batch

AT = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)


def prepared() -> RuleReviewSnapshot:
    return prepare_snapshot(example_rule_batch(), at=AT)


def approved() -> RuleReviewSnapshot:
    return approve_snapshot(
        prepared(),
        candidate_id="virtual-a-b",
        reviewer="virtual-reviewer",
        at=AT,
    )


class ReviewWorkflowTests(unittest.TestCase):
    def test_prepare_and_explicit_approval_leave_previous_artifact_unchanged(self) -> None:
        original = prepared()
        self.assertEqual(original.operation, ReviewOperation.PREPARED)
        self.assertEqual(original.reviews[0].status, "needs_review")
        self.assertEqual(original.reviews[0].issues, ())
        result = approve_snapshot(
            original,
            candidate_id="virtual-a-b",
            reviewer="virtual-reviewer",
            at=AT,
        )
        self.assertEqual(result.operation, ReviewOperation.APPROVED)
        self.assertEqual(result.reviews[0].status, "approved")
        self.assertEqual(result.reviews[0].approval.reviewed_at, AT)
        self.assertIsNone(original.reviews[0].approval)

    def test_invalid_preparation_is_an_error_not_an_empty_success(self) -> None:
        batch = replace(example_rule_batch(), documents=())
        with self.assertRaisesRegex(RuleReviewWorkflowError, "^invalid_input$"):
            prepare_snapshot(batch, at=AT)

    def test_empty_extraction_does_not_create_an_approval(self) -> None:
        result = prepare_snapshot(replace(example_rule_batch(), candidates=()), at=AT)
        self.assertEqual(result.reviews, ())

    def test_missing_or_ambiguous_candidate_is_not_approved(self) -> None:
        original = prepared()
        for snapshot, candidate_id, code in (
            (original, "missing", "review_candidate_not_found"),
            (
                replace(original, reviews=original.reviews * 2),
                "virtual-a-b",
                "review_candidate_ambiguous",
            ),
        ):
            with self.subTest(code=code):
                with self.assertRaisesRegex(RuleReviewWorkflowError, f"^{code}$"):
                    approve_snapshot(snapshot, candidate_id=candidate_id, reviewer="virtual", at=AT)

    def test_manual_concern_survives_inspection_and_blocks_approval(self) -> None:
        original = approved()
        held = replace(original.reviews[0], issues=(ReviewIssue("manual_source_conflict"),))
        snapshot = replace(original, reviews=(held,))
        checked = inspect_snapshot(snapshot)
        self.assertIsNone(checked.reviews[0].approval)
        self.assertIn(ReviewIssue("manual_source_conflict"), checked.reviews[0].issues)
        with self.assertRaisesRegex(RuleReviewWorkflowError, "^candidate_not_approvable$"):
            approve_snapshot(snapshot, candidate_id="virtual-a-b", reviewer="virtual", at=AT)

    def test_source_text_change_revokes_approval_and_preserves_old_snapshot(self) -> None:
        original = approved()
        document = replace(original.batch.documents[0], text="[가상] 변경된 공고")
        current = replace(original.batch, documents=(document,))
        refreshed = refresh_snapshot(original, current, at=AT + timedelta(hours=1))
        self.assertEqual(refreshed.operation, ReviewOperation.REFRESHED)
        self.assertEqual(refreshed.batch, current)
        self.assertIsNone(refreshed.reviews[0].approval)
        self.assertEqual(original.reviews[0].status, "approved")
        self.assertEqual(refreshed.reviews[0].candidate, original.reviews[0].candidate)

    def test_authoritative_empty_or_unofficial_batch_revokes_old_approval(self) -> None:
        original = approved()
        for current in (
            replace(original.batch, documents=(), policies=(), candidates=()),
            replace(
                original.batch, documents=(replace(original.batch.documents[0], official=False),)
            ),
        ):
            with self.subTest(documents=current.documents):
                refreshed = refresh_snapshot(original, current, at=AT)
                self.assertEqual(len(refreshed.reviews), 1)
                self.assertEqual(refreshed.reviews[0].status, "needs_review")
                self.assertTrue(refreshed.reviews[0].issues)

    def test_new_conflicting_candidate_is_not_automatically_imported_as_a_review(self) -> None:
        original = approved()
        new_candidate = replace(original.batch.candidates[0], id="virtual-rival")
        current = replace(original.batch, candidates=(*original.batch.candidates, new_candidate))
        refreshed = refresh_snapshot(original, current, at=AT)
        self.assertEqual(len(refreshed.reviews), 1)
        self.assertIsNone(refreshed.reviews[0].approval)
        self.assertIn(
            "conflicting_candidates", {issue.code for issue in refreshed.reviews[0].issues}
        )

    def test_recollection_preserves_original_review_time(self) -> None:
        original = approved()
        document = replace(original.batch.documents[0], collected_at=AT + timedelta(hours=1))
        refreshed = refresh_snapshot(
            original,
            replace(original.batch, documents=(document,)),
            at=AT + timedelta(hours=2),
        )
        self.assertEqual(refreshed.reviews[0].approval, original.reviews[0].approval)
        self.assertEqual(refreshed.created_at, AT + timedelta(hours=2))
        self.assertEqual(inspect_snapshot(refreshed).created_at, refreshed.created_at)

    def test_pending_concerns_are_not_cleared_by_restoring_the_old_source(self) -> None:
        original = approved()
        changed = replace(original.batch, documents=())
        pending = refresh_snapshot(original, changed, at=AT)
        restored = refresh_snapshot(pending, original.batch, at=AT)
        self.assertIsNone(restored.reviews[0].approval)
        self.assertTrue(restored.reviews[0].issues)

    def test_operation_time_must_be_aware_and_not_backdated(self) -> None:
        original = prepared()
        with self.assertRaisesRegex(RuleReviewWorkflowError, "^review_time_requires_timezone$"):
            prepare_snapshot(original.batch, at=AT.replace(tzinfo=None))
        with self.assertRaisesRegex(RuleReviewWorkflowError, "^review_time_precedes_snapshot$"):
            refresh_snapshot(original, original.batch, at=AT - timedelta(seconds=1))
        future = replace(original.batch.documents[0], collected_at=AT + timedelta(days=1))
        with self.assertRaisesRegex(RuleReviewWorkflowError, "^review_precedes_collection$"):
            prepare_snapshot(replace(original.batch, documents=(future,)), at=AT)


if __name__ == "__main__":
    unittest.main()
