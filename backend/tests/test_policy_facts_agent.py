from datetime import datetime, timezone
from unittest import TestCase
from unittest.mock import Mock

from benefitfinder.ai.policy_facts_agent import extract_policy_facts
from benefitfinder.application.policy_rules import PolicyReference, RuleReviewBatch, SourceDocument


def _batch(*lines: str) -> RuleReviewBatch:
    document = SourceDocument(
        "virtual-notice",
        "virtual-policy",
        "https://example.invalid/notice",
        "v1",
        datetime(2026, 11, 6, tzinfo=timezone.utc),
        "\n".join(lines),
        True,
    )
    policy = PolicyReference("virtual-policy", "youth", ("employment",))
    return RuleReviewBatch((document,), (policy,), ())


def _reply(*lines: int) -> dict[str, object]:
    return {
        "tool_calls": [
            {"function": {"name": "submit_evidence", "arguments": {"lines": list(lines)}}}
        ]
    }


class PolicyFactsAgentTests(TestCase):
    def test_keeps_evidence_and_does_not_infer_total_or_fixed_day(self) -> None:
        amount = "- 월 최대 5만 원씩 6개월 지원"
        bonus = "- 활동을 마치면 별도 5만 원 지원"
        timing = "서류 확인 후 익월 1일 이후 지급"
        note = "* 지원 대상이 아닌 비용은 제외"
        batch = _batch(
            "1. 지원대상",
            "만 19~24세 청년",
            "2. 지원금액",
            amount,
            note,
            bonus,
            "3. 지급방법",
            timing,
        )
        chat = Mock(side_effect=[_reply(2), _reply(4, 5, 6), _reply(8)])
        result = extract_policy_facts(batch, chat=chat)

        self.assertEqual(result.eligibility[0].quote, "만 19~24세 청년")
        self.assertEqual(result.eligibility[0].content_sha256, batch.documents[0].content_sha256)
        self.assertEqual(result.amounts[0].evidence.quote, amount + "\n" + note)
        self.assertEqual(result.amounts[0].amounts_won, (50_000,))
        self.assertEqual(result.amounts[1].evidence.quote, bonus)
        self.assertEqual(result.amounts[1].amounts_won, (50_000,))
        self.assertEqual(result.payment_timing[0].quote, "3. 지급방법\n" + timing)
        self.assertIsNone(result.scheduled_monthly_day)

    def test_monthly_day_is_kept_only_with_explicit_evidence(self) -> None:
        timing = "지급 시기: 매월 25일(공휴일이면 전날) 지급"
        note = "※ 예산에 따라 지급일 변경 가능"
        batch = _batch("1. 지원금액은 미정", "2. " + timing, note)
        result = extract_policy_facts(batch, chat=Mock(side_effect=[_reply(1), _reply(2)]))

        self.assertEqual(result.eligibility, ())
        self.assertEqual(result.amounts, ())
        self.assertEqual(result.scheduled_monthly_day, 25)
        self.assertEqual(result.payment_timing[0].quote, "2. " + timing + "\n" + note)

    def test_rejects_invented_evidence_lines(self) -> None:
        batch = _batch("1. 지원대상", "가상 시에 거주하는 청년")
        chat = Mock(return_value=_reply(12))
        with self.assertRaisesRegex(ValueError, "policy_facts_unverified_submission"):
            extract_policy_facts(batch, chat=chat)

    def test_monthly_day_does_not_turn_alternatives_or_deadlines_into_a_fixed_day(self) -> None:
        for wording in (
            "매월 10일 또는 25일 지급",
            "매월 10일 또는 매월 25일 지급",
            "매월 10일(조기 대상)에 지급 또는 25일 지급",
            "매월 10일~25일 사이에 지급",
            "매월 25일~말일 사이에 지급",
            "매월 25일까지 지급",
            "매월 25일 전후 지급",
        ):
            with self.subTest(wording=wording):
                timing = "지급 시기: " + wording
                result = extract_policy_facts(_batch(timing), chat=Mock(return_value=_reply(1)))
                self.assertEqual(result.payment_timing[0].quote, timing)
                self.assertIsNone(result.scheduled_monthly_day)
