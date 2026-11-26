"""Fictional clauses exercise deterministic normalization of located expenses."""

from datetime import date
import unittest

from benefitfinder.infrastructure.policy_catalog_records import CatalogRecord, validate_source_text
from benefitfinder.ingestion.api_decider_update import decider_update_summary
from benefitfinder.ingestion.decider_expense_cash import compile_decider_expense_cash
from backend.tests.test_api_decider_update import artifact
from backend.tests.test_decider_pending import case


def expense_case(support="응시료의 80%를 지원하며 최대 20만원 한도입니다."):
    value, _, _ = case(
        fields={
            "plcyNm": "가상 청년 응시료 지원",
            "plcySprtCn": support,
            "zipCd": "",
            "ptcpPrpTrgtCn": "",
            "etcMttrCn": "",
            "earnEtcCn": "",
            "lifetime_once": "",
        }
    )
    output = artifact(value)
    output["decisions"] = [
        {"answers": {"amount_kind": {"choice": "cap"}, "recipient": {"choice": "applicant"}}}
    ]
    return value, decider_update_summary(output, artifact_ref="private-fictional/expense.json")


class DeciderExpenseCashTest(unittest.TestCase):
    def test_explicit_percentage_and_cap_use_existing_applicant_facts_rule(self):
        value, summary = expense_case()
        result = compile_decider_expense_cash(value, summary, as_of=date(2026, 11, 17))
        self.assertIsNotNone(result.payload, result.reasons)
        record = CatalogRecord.model_validate(result.payload)
        validate_source_text(record, value.documents[0].text)
        claim = next(
            condition.source_cash
            for condition in record.conditions
            if condition.kind == "source_cash"
        )
        self.assertEqual((claim.amount_won, claim.reimbursement_bps), (200000, 8000))
        self.assertIsNone(record.payments)
        self.assertIn("상한", record.benefit.benefit_amount.amount_text)
        self.assertTrue(result.normalized["calculation_ready"])
        self.assertEqual(result.normalized["ai_review_calls"], 0)
        self.assertTrue(record.benefit.source.document_version.endswith(":decider-expense-v2"))

    def test_cap_without_stated_reimbursement_rate_remains_incomplete(self):
        value, summary = expense_case("응시료 최대 20만원 지원")
        result = compile_decider_expense_cash(value, summary, as_of=date(2026, 11, 17))
        self.assertIsNone(result.payload)
        self.assertEqual(result.reasons, ("decider_expense_reimbursement_rate_missing",))

    def test_explicit_other_benefit_restriction_is_kept_pending(self):
        value, summary = expense_case("응시료 실비 최대 20만원 지원. 다른 사업과 중복 수혜 불가")
        result = compile_decider_expense_cash(value, summary, as_of=date(2026, 11, 17))
        self.assertIsNone(result.payload)
        self.assertEqual(result.reasons, ("decider_expense_overlap_requires_mapping",))

    def test_model_cannot_change_original_rate_or_turn_a_cap_into_a_fixed_grant(self):
        value, summary = expense_case()
        summary["decisions"][0]["amount_kind"]["choice"] = "fixed"
        result = compile_decider_expense_cash(value, summary, as_of=date(2026, 11, 17))
        self.assertIsNone(result.payload)
        self.assertEqual(result.reasons, ("decider_expense_not_a_cap",))


if __name__ == "__main__":
    unittest.main()
