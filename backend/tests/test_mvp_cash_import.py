"""Import shape and existing calculation behavior using fictional parser output."""

from datetime import date
from types import SimpleNamespace
import unittest

from backend.tests.test_api_decider_update import artifact
from backend.tests.test_decider_pending import case
from benefitfinder.domains.youth.source_cash import source_cash_payments
from benefitfinder.infrastructure.policy_catalog_records import CatalogRecord, validate_source_text
from benefitfinder.ingestion.api_decider_update import decider_update_summary
from benefitfinder.ingestion.mvp_cash_import import compile_mvp_cash_import


def sample(parsed=None, *, source_missing_at=None):
    value, _, _ = case(
        fields={
            "plcySprtCn": "가상 응시료의 50%를 최대 20만원 지원",
            "zipCd": "",
            "ptcpPrpTrgtCn": "",
            "etcMttrCn": "",
            "earnEtcCn": "",
        }
    )
    result = compile_mvp_cash_import(
        value,
        decider_update_summary(artifact(value), artifact_ref="fictional/native.json"),
        as_of=date(2026, 11, 18),
        parsed=parsed,
        artifact_ref="fictional/parser.json",
        source_missing_at=source_missing_at,
    )
    record = CatalogRecord.model_validate(result.payload)
    validate_source_text(record, value.documents[0].text)
    return value, record


class MvpCashImportTest(unittest.TestCase):
    def test_unknown_amount_is_null_with_display_text_and_no_payments(self):
        _, record = sample()
        self.assertEqual(record.benefit.benefit_amount.amount_status, "unknown")
        self.assertEqual(record.benefit.benefit_amount.amount_text, "금액 확인불가")
        self.assertIsNone(record.benefit.benefit_amount.amount_won)
        self.assertIsNone(record.payments)
        self.assertFalse(record.benefit.reviewed)
        self.assertTrue(record.unresolved_combination_rules)

    def test_parser_result_is_preserved_when_no_cash_claim_exists(self):
        parsed = {"claim": None, "money_facts": [{"kind": "loan", "amount_won": 10000000}]}
        _, record = sample(parsed)
        self.assertEqual(record.benefit.domain_data["gpt6_analysis"]["result"], parsed)
        self.assertIsNone(record.benefit.benefit_amount.amount_won)

    def test_missing_source_keeps_unknown_history_without_faking_a_cash_payment(self):
        _, record = sample(source_missing_at="2026-11-18T00:00:00+00:00")
        self.assertIsNone(record.benefit.benefit_amount.amount_won)
        self.assertEqual(record.benefit.domain_data["source_presence"]["status"], "missing")
        self.assertFalse(record.benefit.reviewed)

    def test_direct_expense_component_uses_existing_calculator(self):
        parsed = {
            "claim": {
                "mode": "expense",
                "amount_won": 200000,
                "reimbursement_bps": 5000,
                "component_label": "가상 응시료",
                "scope": "실제 비용 1건분",
                "evidence": [{"field": "plcySprtCn", "quote": "응시료의 50%를 최대 20만원 지원"}],
                "manual_terms": [],
            }
        }
        value, record = sample(parsed)
        fact = SimpleNamespace(
            policy_id=value.policy_id,
            eligible_cost_won=65000,
            payment_month="2026-12",
            prior_support_won=0,
            other_reimbursement_won=0,
            other_exclusions_clear=True,
        )
        profile = SimpleNamespace(
            domain_data=SimpleNamespace(cash_facts=SimpleNamespace(source_cash=[fact]))
        )
        self.assertEqual(
            source_cash_payments(
                profile,
                record.published(value.documents[0].collected_at),
                date(2026, 11, 18),
            ),
            ((1, 32500),),
        )
        self.assertIsNone(record.payments)
        self.assertTrue(record.benefit.reviewed)
