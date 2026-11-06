"""F12 rule extraction tests using virtual notices."""

from datetime import datetime, timezone
from unittest import TestCase
from unittest.mock import Mock

from benefitfinder.ai.policy_rule_agent import extract_rule_batch
from benefitfinder.application.policy_rules import PolicyReference, RuleReviewBatch, SourceDocument
from benefitfinder.ingestion.rule_review_workflow import prepare_snapshot


TARGET_NAME = "가상 달빛 수당"
TARGET_ALIAS = "달빛 수당"
UNKNOWN_ALIAS = "가상 별빛 수당"
SOURCE_ID = "policy-f12-src"
TARGET_ID = "policy-f12-tgt"

LINES = (
    "가상 하늘 지원금 안내 첫째 줄",
    "달빛 수당 수혜 중인 경우에는 본 지원금 신청이 제한됩니다.",
    "단, 가상 단기 과정 연계 참여는 예외로 인정합니다.",
    "신청 기간은 수혜 기간과 동일합니다.",
    "기타 안내는 생략합니다.",
)
EXPECTED_QUOTE = "\n".join(LINES[1:3])
EXCEPTION_PHRASE = "가상 단기 과정 연계 참여는 예외로 인정"
CONDITION_CLEAN = "달빛 수당 수혜 중"
CONDITION_MIXED = "달빛 수당 수혜 중, 단, 가상 단기 과정 연계 참여는 예외로 인정"


def reply(*calls: tuple[str, dict[str, object]]) -> dict[str, object]:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": name, "arguments": args}} for name, args in calls],
    }


def make_batch(now: datetime) -> RuleReviewBatch:
    return RuleReviewBatch(
        (
            SourceDocument(
                "doc-f12-01",
                SOURCE_ID,
                "https://example.invalid/f12-01",
                "v2026-f12",
                now,
                "\n".join(LINES),
                True,
            ),
        ),
        (
            PolicyReference(SOURCE_ID, "youth", ("employment",)),
            PolicyReference(TARGET_ID, "youth", ("employment",)),
        ),
        (),
    )


def good_submit() -> dict[str, object]:
    return {
        "start_line": 2,
        "end_line": 3,
        "kind": "conditional",
        "direction": "target_to_source",
        "condition": CONDITION_CLEAN,
        "exceptions": [EXCEPTION_PHRASE],
        "period": "수혜 중",
        "amount_effect": "",
    }


def bad_submit() -> dict[str, object]:
    return {
        "start_line": 2,
        "end_line": 3,
        "kind": "conditional",
        "direction": "target_to_source",
        "condition": CONDITION_MIXED,
        "exceptions": [],
        "period": "수혜 중",
        "amount_effect": "",
    }


class PolicyRuleAgentF12Tests(TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 11, 6, tzinfo=timezone.utc)
        self.batch = make_batch(self.now)

    def test_multiline_alias_exception_success(self) -> None:
        chat = Mock(
            side_effect=[
                reply(
                    ("lookup_policy", {"name": "달빛   수당"}),
                    ("find_clauses", {"query": "신청이 제한"}),
                ),
                reply(("submit_rule", good_submit())),
            ]
        )
        batch, trace = extract_rule_batch(
            self.batch,
            target_name=TARGET_NAME,
            target_id=TARGET_ID,
            chat=chat,
            target_aliases=(TARGET_ALIAS,),
        )
        (candidate,) = batch.candidates
        self.assertEqual(trace[-1], "submit_rule")
        self.assertEqual(set(trace), {"lookup_policy", "find_clauses", "submit_rule"})
        self.assertEqual(candidate.description, EXPECTED_QUOTE)
        self.assertEqual(candidate.citations[0].quote, EXPECTED_QUOTE)
        self.assertEqual(
            candidate.citations[0].content_sha256, self.batch.documents[0].content_sha256
        )
        self.assertEqual(candidate.conditions, (CONDITION_CLEAN,))
        self.assertEqual(candidate.exceptions, (EXCEPTION_PHRASE,))
        self.assertNotIn("단,", candidate.conditions[0])
        snapshot = prepare_snapshot(batch, at=self.now)
        (review,) = snapshot.reviews
        self.assertEqual(review.status, "needs_review")
        self.assertIsNone(review.approval)

    def test_unknown_alias_stays_unresolved(self) -> None:
        chat = Mock(
            side_effect=[
                reply(
                    ("lookup_policy", {"name": UNKNOWN_ALIAS}),
                    ("find_clauses", {"query": "신청이 제한"}),
                ),
                reply(("submit_rule", good_submit())),
            ]
        )
        with self.assertRaisesRegex(ValueError, "agent_unverified_submission"):
            extract_rule_batch(
                self.batch,
                target_name=TARGET_NAME,
                target_id=TARGET_ID,
                chat=chat,
                target_aliases=(TARGET_ALIAS,),
            )

    def test_mixed_exception_is_not_adopted_before_correction(self) -> None:
        self.assertIn(EXCEPTION_PHRASE, str(bad_submit()["condition"]))
        chat = Mock(
            side_effect=[
                reply(
                    ("lookup_policy", {"name": TARGET_ALIAS}),
                    ("find_clauses", {"query": "신청이 제한"}),
                ),
                reply(("submit_rule", bad_submit())),
                {"role": "assistant", "content": "가상 일반 답변"},
                reply(("submit_rule", good_submit())),
            ]
        )
        batch, trace = extract_rule_batch(
            self.batch,
            target_name=TARGET_NAME,
            target_id=TARGET_ID,
            chat=chat,
            target_aliases=(TARGET_ALIAS,),
        )
        self.assertEqual(chat.call_count, 4)
        self.assertEqual(trace[-1], "submit_rule")
        (candidate,) = batch.candidates
        self.assertEqual(candidate.description, EXPECTED_QUOTE)
        self.assertEqual(candidate.conditions, (CONDITION_CLEAN,))
        self.assertEqual(candidate.exceptions, (EXCEPTION_PHRASE,))
        self.assertNotIn("단,", candidate.conditions[0])
        self.assertNotIn(EXCEPTION_PHRASE, candidate.conditions[0])

    def test_rejects_same_turn_submission_without_prior_evidence(self) -> None:
        chat = Mock(
            return_value=reply(
                ("find_clauses", {"query": "신청이 제한"}),
                ("lookup_policy", {"name": TARGET_ALIAS}),
                ("submit_rule", good_submit()),
            )
        )
        with self.assertRaisesRegex(ValueError, "agent_unverified_submission"):
            extract_rule_batch(
                self.batch,
                target_name=TARGET_NAME,
                target_id=TARGET_ID,
                chat=chat,
                target_aliases=(TARGET_ALIAS,),
            )

    def test_refuses_unknown_catalog_target_before_calling_model(self) -> None:
        chat = Mock()
        with self.assertRaisesRegex(ValueError, "agent_policy_not_in_scope"):
            extract_rule_batch(
                self.batch,
                target_name=TARGET_NAME,
                target_id="invented-f12",
                chat=chat,
                target_aliases=(TARGET_ALIAS,),
            )
        chat.assert_not_called()
