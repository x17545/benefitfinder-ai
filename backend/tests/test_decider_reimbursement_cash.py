"""Fictional source clauses exercise one actual-cost component, without AI review."""

from datetime import date
from types import SimpleNamespace
import unittest

from backend.tests.test_api_decider_update import artifact
from backend.tests.test_decider_pending import case
from benefitfinder.domains.youth.source_cash import source_cash_payments
from benefitfinder.infrastructure.policy_catalog_records import CatalogRecord, validate_source_text
from benefitfinder.ingestion.api_decider_update import decider_update_summary
from benefitfinder.ingestion.decider_reimbursement_cash import compile_decider_reimbursement_cash


def compile_case(title, support):
    value, _, _ = case(
        fields={
            "plcyNm": title,
            "plcySprtCn": support,
            "zipCd": "",
            "ptcpPrpTrgtCn": "",
            "etcMttrCn": "",
            "earnEtcCn": "",
            "lifetime_once": "",
        }
    )
    native = artifact(value)
    # A mistaken semantic label must never override the actual source cap.
    native["decisions"] = [{"answers": {"amount_kind": {"choice": "fixed"}}}]
    summary = decider_update_summary(native, artifact_ref="private-fictional/cost.json")
    return value, compile_decider_reimbursement_cash(value, summary, as_of=date(2026, 11, 17))


class DeciderReimbursementCashTest(unittest.TestCase):
    def test_qualifying_transaction_price_is_not_a_reimbursement_cap(self):
        _, result = compile_case(
            "가상 청년 중개수수료 지원",
            "임대차 거래금액 2억원 이하 주택\n중개보수 납부액의 50%(최대 150천원)",
        )
        record = CatalogRecord.model_validate(result.payload)
        rule = next(r.source_cash for r in record.conditions if r.kind == "source_cash")
        self.assertEqual((rule.amount_won, rule.reimbursement_bps), (150000, 5000))

    def test_other_program_discount_is_not_applied_twice_to_actual_paid_cost(self):
        _, result = compile_case(
            "가상 청년 응시료 지원",
            "실제 본인이 부담한 응시료 실비를 최대 20만원 지원.\n"
            "다른 기관에서 응시료의 50%를 감면 지원받은 경우 차액(실제 결제액)을 지원합니다.",
        )
        record = CatalogRecord.model_validate(result.payload)
        rule = next(r.source_cash for r in record.conditions if r.kind == "source_cash")
        self.assertEqual(rule.reimbursement_bps, 10000)

    def test_monthly_cost_cap_is_one_component_and_total_is_not_a_fixed_payment(self):
        value, result = compile_case(
            "가상 청년 월세 지원",
            "실제 납부하는 월세 범위 내 월 최대 20만원\n최대 24개월, 총 최대 480만원 지원",
        )
        record = CatalogRecord.model_validate(result.payload)
        validate_source_text(record, value.documents[0].text)
        rule = next(r.source_cash for r in record.conditions if r.kind == "source_cash")
        self.assertEqual(
            (rule.amount_won, rule.mode, rule.reimbursement_bps), (200000, "expense", 10000)
        )
        self.assertIsNone(record.payments)
        self.assertIn("1개월분", record.benefit.benefit_amount.amount_text)
        self.assertNotIn("evidence", record.benefit.domain_data["decider_analysis"])
        self.assertTrue(any(r.kind == "manual" for r in record.conditions))

    def test_explicit_rate_and_applicant_cost_use_existing_calculator(self):
        value, result = compile_case(
            "가상 청년 임차료 지원", "임차료의 50% 지원(월 최대 20만원/6개월 간)"
        )
        record = CatalogRecord.model_validate(result.payload)
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
                profile, record.published(value.documents[0].collected_at), date(2026, 11, 17)
            ),
            ((1, 32500),),
        )
        fact.eligible_cost_won = 900000
        self.assertEqual(
            source_cash_payments(
                profile, record.published(value.documents[0].collected_at), date(2026, 11, 17)
            ),
            ((1, 200000),),
        )
        fact.eligible_cost_won = None
        self.assertIsNone(
            source_cash_payments(
                profile, record.published(value.documents[0].collected_at), date(2026, 11, 17)
            )
        )

    def test_unstated_rate_and_principal_percentage_do_not_become_reimbursements(self):
        for title, text in (
            ("가상 응시료 지원", "응시료 최대 20만원 지원"),
            ("가상 대출이자 지원", "대출금 잔액의 2.5% 지원(연 최대 20만원)"),
        ):
            with self.subTest(text=text):
                _, result = compile_case(title, text)
                self.assertIsNone(result.payload)

    def test_overlap_and_alternative_caps_remain_unresolved(self):
        for text in (
            "실비 최대 20만원. 다른 사업과 중복 수혜 불가",
            "기납부한 보증료 전부 또는 일부, 최대 20만원 환급",
            "실제 납부한 월세 월 최대 20만원 또는 최대 30만원 지원",
        ):
            with self.subTest(text=text):
                _, result = compile_case("가상 청년 월세 지원", text)
                self.assertIsNone(result.payload)


if __name__ == "__main__":
    unittest.main()
