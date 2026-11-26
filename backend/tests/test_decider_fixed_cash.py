"""Fictional fixed API grants compiled without model validation or schedules."""

from dataclasses import replace
from datetime import date
import unittest

from benefitfinder.application.region_options import DistrictOption, RegionOption
from benefitfinder.infrastructure.policy_catalog_records import CatalogRecord, validate_source_text
from benefitfinder.ingestion.api_decider_update import decider_update_summary
from benefitfinder.ingestion.decider_fixed_cash import compile_decider_fixed_cash
from backend.tests.test_api_decider_update import artifact
from backend.tests.test_decider_pending import case


def fixed_case(
    support: str,
    *,
    title: str = "가상 청년 지원금",
    extra: dict | None = None,
):
    fields = {
        "plcyNm": title,
        "plcySprtCn": support,
        "zipCd": "",
        "ptcpPrpTrgtCn": "과거 가상 동일 지원금을 받은 사람은 제외합니다.",
        "etcMttrCn": "",
    }
    fields.update(extra or {})
    value, _, _ = case(fields=fields)
    summary = decider_update_summary(
        artifact(value), artifact_ref="private-fictional/fixed-cash.json"
    )
    return value, summary


class DeciderFixedCashTest(unittest.TestCase):
    def test_fixed_once_without_calendar_uses_applicant_payment_month(self):
        value, summary = fixed_case("가상 지역 정착지원금 100만원을 1회 지급합니다.")
        result = compile_decider_fixed_cash(value, summary, as_of=date(2026, 11, 17))
        self.assertIsNotNone(result.payload, result.reasons)
        record = CatalogRecord.model_validate(result.payload)
        validate_source_text(record, value.documents[0].text)
        rule = next(rule.source_cash for rule in record.conditions if rule.kind == "source_cash")
        self.assertEqual(
            (rule.mode, rule.amount_won, rule.reimbursement_bps), ("fixed", 1_000_000, 10000)
        )
        self.assertIsNone(record.payments)
        self.assertEqual(record.benefit.benefit_amount.amount_won, 1_000_000)
        self.assertEqual(record.benefit.benefit_amount.period_months, 1)
        self.assertEqual(record.benefit.benefit_amount.pay_timing, "once")
        self.assertTrue(record.benefit.source.document_version.endswith(":decider-fixed-v1"))
        self.assertTrue(result.normalized["requires_applicant_cash_runtime"])

    def test_monthly_fixed_component_is_one_month_not_the_stated_twelve(self):
        value, summary = fixed_case("가상 청년 수당은 월 5만원씩 최대 12개월 지원합니다.")
        result = compile_decider_fixed_cash(value, summary, as_of=date(2026, 11, 17))
        self.assertIsNotNone(result.payload, result.reasons)
        record = CatalogRecord.model_validate(result.payload)
        amount = record.benefit.benefit_amount
        self.assertEqual(
            (amount.amount_won, amount.period_months, amount.max_total_won), (50_000, 1, 50_000)
        )
        self.assertIn("1개월분", amount.amount_text)
        self.assertIsNone(record.payments)
        self.assertEqual(result.normalized["claim_count"], 1)

    def test_cap_is_not_promoted_to_a_fixed_grant(self):
        value, summary = fixed_case("가상 청년 지원금은 최대 50만원을 지원합니다.")
        result = compile_decider_fixed_cash(value, summary, as_of=date(2026, 11, 17))
        self.assertIsNone(result.payload)
        self.assertEqual(result.reasons, ("decider_fixed_cap_not_supported",))

    def test_salary_and_employer_amounts_are_rejected(self):
        for support in (
            "가상 청년 급여조건 월 200만원 이상 지원합니다.",
            "가상 청년을 채용한 사업주에게 월 50만원을 지원합니다.",
        ):
            with self.subTest(support=support):
                value, summary = fixed_case(support)
                result = compile_decider_fixed_cash(value, summary, as_of=date(2026, 11, 17))
                self.assertIsNone(result.payload)
                self.assertEqual(result.reasons, ("decider_fixed_not_personal_cash",))

    def test_alternative_amounts_are_never_guessed(self):
        value, summary = fixed_case("가상 청년 지원금 50만원 또는 100만원을 지원합니다.")
        result = compile_decider_fixed_cash(value, summary, as_of=date(2026, 11, 17))
        self.assertIsNone(result.payload)
        self.assertEqual(result.reasons, ("decider_fixed_multiple_components",))

    def test_one_independent_monthly_grant_is_partial_beside_budget_and_service(self):
        value, summary = fixed_case(
            "가상 청년 교통비 월 5만원 지원, 최대 12개월\n"
            "가상 사업 예산 1억원\n가상 취업 서비스는 무료로 제공합니다."
        )
        result = compile_decider_fixed_cash(value, summary, as_of=date(2026, 11, 17))
        self.assertIsNotNone(result.payload, result.reasons)
        record = CatalogRecord.model_validate(result.payload)
        self.assertTrue(result.normalized["partial_component"])
        self.assertIn("부분 계산", record.benefit.benefit_amount.amount_text)
        self.assertEqual(record.benefit.benefit_amount.amount_won, 50_000)
        self.assertNotIn("1억", record.benefit.benefit_amount.amount_text)

    def test_evidence_manual_terms_regions_lifetime_and_missing_facts_are_preserved(self):
        raw_title = "가상 청년 정착지원금 "
        value, summary = fixed_case(
            "가상 청년 정착지원금 100만원, 생애 1회 지원합니다.",
            title=raw_title,
            extra={"zipCd": "11110"},
        )
        value = replace(
            value,
            policies=(replace(value.policies[0], name=value.policies[0].name.strip()),),
        )
        regions = (
            RegionOption("11", "가상광역시", (DistrictOption("11110", "가상구"),)),
            RegionOption("26", "가상도", (DistrictOption("26110", "가상시"),)),
        )
        result = compile_decider_fixed_cash(
            value, summary, as_of=date(2026, 11, 17), regions=regions
        )
        self.assertIsNotNone(result.payload, result.reasons)
        record = CatalogRecord.model_validate(result.payload)
        validate_source_text(record, value.documents[0].text)
        self.assertEqual(record.benefit.title, raw_title)
        self.assertTrue(record.benefit.lifetime_once)
        self.assertTrue(
            any(
                rule.kind == "none_of" and rule.field == "domain_data.received"
                for rule in record.conditions
            )
        )
        self.assertTrue(any(rule.kind == "selection" for rule in record.conditions))
        self.assertTrue(
            any(
                rule.kind == "selection" and rule.field == "domain_data.region.sido_code"
                for rule in record.conditions
            )
        )
        self.assertIsNone(record.payments)
        self.assertTrue(record.payment_evidence)
        self.assertTrue(
            all(item.quote in value.documents[0].text for item in record.payment_evidence)
        )
        self.assertTrue(
            any(
                "과거 가상 동일 지원금" in evidence.quote
                for condition in record.benefit.eligibility_conditions
                for evidence in condition.evidence
            )
        )
        analysis = record.benefit.domain_data["decider_analysis"]
        self.assertEqual(analysis["evidence_count"], 1)
        self.assertNotIn("evidence", analysis)
        self.assertIn("지급예정월", record.benefit.summary)
        self.assertTrue(result.normalized["applicant_payment_month_required"])

    def test_overlap_restriction_remains_a_hold_without_a_conflict_mapping(self):
        value, summary = fixed_case(
            "가상 청년 지원금 20만원을 1회 지급합니다.",
            extra={"etcMttrCn": "가상 사업과 중복 수혜는 불가합니다."},
        )
        result = compile_decider_fixed_cash(value, summary, as_of=date(2026, 11, 17))
        self.assertIsNone(result.payload)
        self.assertEqual(result.reasons, ("decider_fixed_overlap_requires_mapping",))

    def test_unallocated_employer_and_youth_incentive_is_not_applicant_cash(self):
        value, summary = fixed_case(
            "가상 청년 정규직 전환 인센티브는 400만원을 지원합니다(기업 및 청년)."
        )
        result = compile_decider_fixed_cash(value, summary, as_of=date(2026, 11, 17))
        self.assertIsNone(result.payload)
        self.assertEqual(result.reasons, ("decider_fixed_not_personal_cash",))

    def test_separately_labelled_youth_component_is_valid(self):
        value, summary = fixed_case(
            "(기업) 정규직 채용 시 월 60만원 지원\n(청년) 정규직 전환 인센티브 60만원 지원"
        )
        result = compile_decider_fixed_cash(value, summary, as_of=date(2026, 11, 17))
        self.assertIsNotNone(result.payload, result.reasons)
        record = CatalogRecord.model_validate(result.payload)
        self.assertEqual(record.benefit.benefit_amount.amount_won, 600_000)
        self.assertTrue(result.normalized["partial_component"])
        self.assertIn("부분 계산", record.benefit.benefit_amount.amount_text)


if __name__ == "__main__":
    unittest.main()
