from datetime import datetime, timezone
from unittest import TestCase
from unittest.mock import Mock

from benefitfinder.ai.policy_rule_agent import extract_rule_batch
from benefitfinder.application.policy_rules import PolicyReference, RuleReviewBatch, SourceDocument
from benefitfinder.ingestion.rule_review_workflow import prepare_snapshot


def reply(*calls: tuple[str, dict[str, object]]) -> dict[str, object]:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": name, "arguments": args}} for name, args in calls],
    }


class PolicyRuleAgentTests(TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 11, 6, tzinfo=timezone.utc)
        self.clause = "가상 B 수혜 중에는 참여 제한 (가상 단기 과정 연계는 예외)."
        self.batch = RuleReviewBatch(
            (
                SourceDocument(
                    "notice-a",
                    "policy-a",
                    "https://example.test/a",
                    "v1",
                    self.now,
                    "가상 B 안내 제목\n" + self.clause,
                    True,
                ),
            ),
            tuple(
                PolicyReference(key, "youth", ("employment",)) for key in ("policy-a", "policy-b")
            ),
            (),
        )
        self.submit: dict[str, object] = {
            "line": 2,
            "kind": "conditional",
            "direction": "target_to_source",
            "condition": "가상 B 수혜 중",
            "exception": "가상 단기 과정 연계",
            "period": "수혜 중",
            "amount_effect": "",
        }

    def test_evidence_from_tools_reaches_existing_review_without_approval(self) -> None:
        chat = Mock(
            side_effect=[
                reply(
                    ("lookup_policy", {"name": "가상 B"}), ("find_clauses", {"query": "수혜 중"})
                ),
                reply(("submit_rule", self.submit)),
            ]
        )
        batch, trace = extract_rule_batch(
            self.batch, target_name="가상 B", target_id="policy-b", chat=chat
        )
        snapshot = prepare_snapshot(batch, at=self.now)
        (review,) = snapshot.reviews
        self.assertEqual(trace, ("lookup_policy", "find_clauses", "submit_rule"))
        self.assertEqual(review.candidate.citations[0].quote, self.clause)
        self.assertEqual(
            review.candidate.citations[0].content_sha256, self.batch.documents[0].content_sha256
        )
        self.assertEqual(review.candidate.exceptions, ("가상 단기 과정 연계",))
        self.assertEqual(review.status, "needs_review")
        self.assertIsNone(review.approval)
        self.assertEqual({issue.code for issue in review.issues}, {"unsupported_rule"})

    def test_rejects_submission_that_did_not_get_evidence_in_a_previous_turn(self) -> None:
        chat = Mock(
            return_value=reply(
                ("find_clauses", {"query": "수혜 중"}),
                ("lookup_policy", {"name": "가상 B"}),
                ("submit_rule", self.submit),
            )
        )
        with self.assertRaisesRegex(ValueError, "agent_unverified_submission"):
            extract_rule_batch(self.batch, target_name="가상 B", target_id="policy-b", chat=chat)

    def test_refuses_unknown_catalog_target_before_calling_model(self) -> None:
        chat = Mock()
        with self.assertRaisesRegex(ValueError, "agent_policy_not_in_scope"):
            extract_rule_batch(self.batch, target_name="가상 B", target_id="invented", chat=chat)
        chat.assert_not_called()
