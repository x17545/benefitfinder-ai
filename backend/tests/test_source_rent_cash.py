"""Fictional regressions for deterministic dynamic rent-support publication."""

from dataclasses import replace
from datetime import date, datetime, timezone
from types import SimpleNamespace
import unittest

from benefitfinder.application.policy_analysis import (
    AnalysisPolicy,
    AnalysisSource,
    PolicyAnalysisInput,
)
from benefitfinder.application.policy_rules import SourceDocument
from benefitfinder.application.region_options import DistrictOption, RegionOption
from benefitfinder.domains.youth.rent_support import rent_support_payments
from benefitfinder.infrastructure.policy_catalog_records import CatalogRecord, validate_source_text
from benefitfinder.ingestion.source_rent_cash import compile_source_rent_cash


ID = "999000123"
POLICY_ID = "youth:youthcenter:" + ID
REGIONS = (
    RegionOption(
        "11",
        "가상도",
        (DistrictOption("11110", "가상시"), DistrictOption("11200", "가상군")),
    ),
    RegionOption("41", "다른도", (DistrictOption("41110", "다른시"),)),
)


def sample(*, remove: tuple[str, ...] = (), **changes: str) -> PolicyAnalysisInput:
    fields = {
        "plcyNo": ID,
        "plcyNm": "[가상] 청년월세 특별지원(2차)",
        "lclsfNm": "주거",
        "mclsfNm": "주거비지원",
        "rgtrInstCdNm": "가상 기관",
        "sprtTrgtAgeLmtYn": "N",
        "sprtTrgtMinAge": "18",
        "sprtTrgtMaxAge": "36",
        "jobCd": "0013003",
        "schoolCd": "0049005",
        "mrgSttsCd": "0055002",
        "zipCd": "11110",
        "aplyPrdSeCd": "0057001",
        "aplyYmd": "20260101 ~ 20261231",
        "earnCndSeCd": "0043003",
        "earnMinAmt": "0",
        "earnMaxAmt": "0",
        "earnEtcCn": (
            "청년가구 : 기준 중위소득 65% 이하이며 재산가액이 1.11억원 이하, "
            "원가구 : 기준 중위소득 105% 이하이면서 재산가액이 4.2억원 이하"
        ),
        "addAplyQlfcCndCn": (
            "연령조건 : 만 18~36세\n"
            "청약통장가입\n"
            "부모로부터 독립한 무주택 청년가구\n"
            "청년가구 : 기준 중위소득 65% 이하이며 재산가액이 1.11억원 이하\n"
            "원가구 : 기준 중위소득 105% 이하이면서 재산가액이 4.2억원 이하\n"
            "다만, 31세 이상이거나 혼인 등 부모와 생계를 달리하는 청년은 청년 본인가구의 소득·재산만 확인"
        ),
        "ptcpPrpTrgtCn": "가상 참여 제한은 신청 전에 확인합니다.",
        "plcySprtCn": (
            "실제 납부하는 임대료 범위 내에서 월 최대 15만원씩 12개월 동안 매월 분할 지원\n"
            "주거급여수급자도 월세지원 한도액 15만원에서 주거급여액을 차감하고 지원\n"
            "지급기간 : 2026년 3월 ~ 2027년 12월"
        ),
        "etcMttrCn": "가상 유의사항을 확인합니다. " * 220,
    }
    fields.update(changes)
    for name in remove:
        fields.pop(name, None)
    text = "\n\n".join(f"[{name}]\n{value}" for name, value in fields.items())
    doc = SourceDocument(
        "fictional-rent-document",
        POLICY_ID,
        "https://catalog.example/rent-support",
        "fictional-v1",
        datetime(2026, 11, 16, tzinfo=timezone.utc),
        text,
        True,
    )
    return PolicyAnalysisInput(
        POLICY_ID,
        (doc,),
        (AnalysisPolicy(POLICY_ID, fields["plcyNm"], ("housing",)),),
        (AnalysisSource("youthcenter", ID, "a" * 64),),
    )


def evidence_order(evidence: list) -> list:
    def start(item) -> int:
        return int(item.location.removeprefix("chars:").split("-", 1)[0])

    return sorted(evidence, key=start)


class SourceRentCashTest(unittest.TestCase):
    def test_dynamic_cap_and_complete_source_are_published_without_a_model(self):
        value = sample()
        compiled = compile_source_rent_cash(value, as_of=date(2026, 6, 1), regions=REGIONS)

        self.assertIsNotNone(compiled.payload, compiled.reasons)
        record = CatalogRecord.model_validate(compiled.payload)
        validate_source_text(record, value.documents[0].text)
        amount = record.benefit.benefit_amount
        self.assertEqual(amount.type, "monthly")
        self.assertEqual(amount.amount_won, 150000)
        self.assertEqual(amount.period_months, 12)
        self.assertEqual(amount.max_total_won, 1800000)
        self.assertEqual(amount.pay_timing, "monthly_from_start")
        self.assertIsNone(record.payments)
        self.assertTrue(record.benefit.reviewed)
        self.assertFalse(record.unresolved_combination_rules)
        self.assertEqual(compiled.normalized["model_calls"], 0)
        self.assertEqual(compiled.normalized["ai_review_calls"], 0)
        self.assertTrue(compiled.normalized["full_notice_preserved_in_manual_terms"])
        self.assertTrue(record.benefit.source.document_version.endswith(":source-facts-v1"))

        ordered = evidence_order(record.payment_evidence)
        self.assertEqual(ordered[0].location, f"chars:0-{len(ordered[0].quote)}")
        self.assertEqual("".join(item.quote for item in ordered), value.documents[0].text)

    def test_verified_api_codes_and_region_join_the_existing_rent_rule(self):
        compiled = compile_source_rent_cash(sample(), as_of=date(2026, 6, 1), regions=REGIONS)
        record = CatalogRecord.model_validate(compiled.payload)
        rules = {rule.condition_id: rule for rule in record.conditions}

        self.assertEqual(rules["selection_employment_status"].values, ["job_seeker"])
        self.assertEqual(rules["selection_domain_data_school"].values, ["university_enrolled"])
        self.assertEqual(rules["selection_domain_data_marital"].values, ["single"])
        self.assertEqual(rules["selection_region_code"].field, "region_code")
        self.assertEqual(rules["selection_region_code"].values, ["11110"])
        self.assertNotIn("residence", rules)
        self.assertEqual([rule.condition_id for rule in record.conditions].count("rent_support"), 1)

    def test_recruitment_status_uses_as_of_instead_of_a_fixed_upcoming_value(self):
        for day, expected in (
            (date(2025, 12, 1), "upcoming"),
            (date(2026, 6, 1), "open"),
            (date(2027, 1, 1), "closed"),
        ):
            with self.subTest(day=day):
                compiled = compile_source_rent_cash(sample(), as_of=day, regions=REGIONS)
                record = CatalogRecord.model_validate(compiled.payload)
                self.assertEqual(record.benefit.application.status, expected)
                self.assertEqual(record.benefit.application.starts_on, "2026-01-01")
                self.assertEqual(record.benefit.application.ends_on, "2026-12-31")

    def test_deployed_calculator_resolves_the_cap_from_applicant_values(self):
        compiled = compile_source_rent_cash(sample(), as_of=date(2026, 6, 1), regions=REGIONS)
        record = CatalogRecord.model_validate(compiled.payload)
        policy = record.published(datetime(2026, 6, 1, tzinfo=timezone.utc))
        facts = SimpleNamespace(
            youth_income_ratio_bps=6500,
            youth_assets_won=111000000,
            original_income_ratio_bps=10500,
            original_assets_won=420000000,
            original_household_exempt=False,
            subscription_account=True,
            independent_from_parents=True,
            other_exclusions_clear=True,
            monthly_rent_won=300000,
            housing_benefit_rent_won=50000,
            requested_months=12,
            prior_supported_months=0,
            first_payment_month="2026-07",
        )
        profile = SimpleNamespace(
            age=25,
            domain_data=SimpleNamespace(
                marital="single", cash_facts=SimpleNamespace(rent_support=facts)
            ),
        )

        payments = rent_support_payments(profile, policy, date(2026, 6, 1))

        self.assertEqual(payments, tuple((month, 100000) for month in range(1, 13)))
        self.assertNotEqual(sum(amount for _, amount in payments), 1800000)


class SourceRentCashHoldTest(unittest.TestCase):
    def assert_held(self, compiled, reason: str) -> None:
        self.assertIsNone(compiled.payload)
        self.assertEqual(compiled.reasons, (reason,))
        self.assertFalse(compiled.normalized["calculation_ready"])
        self.assertEqual(compiled.normalized["model_calls"], 0)

    def test_unresolved_overlap_is_held(self):
        compiled = compile_source_rent_cash(
            sample(etcMttrCn="가상 다른 지원금과 중복 수급 불가"),
            as_of=date(2026, 6, 1),
            regions=REGIONS,
        )
        self.assert_held(compiled, "source_rent_cash_overlap_requires_review")

    def test_missing_strict_candidate_terms_are_not_generalized(self):
        for field in ("earnEtcCn", "addAplyQlfcCndCn", "plcySprtCn"):
            with self.subTest(field=field):
                compiled = compile_source_rent_cash(
                    sample(remove=(field,)), as_of=date(2026, 6, 1), regions=REGIONS
                )
                self.assert_held(compiled, "source_rent_cash_supported_terms_required")

    def test_mismatched_household_copy_is_not_silently_accepted(self):
        value = sample()
        text = value.documents[0].text.replace("65%", "66%", 1)
        value = replace(
            value,
            documents=(replace(value.documents[0], text=text),),
        )
        compiled = compile_source_rent_cash(value, as_of=date(2026, 6, 1), regions=REGIONS)
        self.assert_held(compiled, "source_rent_cash_supported_terms_required")

    def test_unknown_region_and_application_dates_are_held(self):
        compiled = compile_source_rent_cash(
            sample(zipCd="99999"), as_of=date(2026, 6, 1), regions=REGIONS
        )
        self.assert_held(compiled, "source_rent_cash_region_not_executable")
        compiled = compile_source_rent_cash(
            sample(remove=("aplyYmd",)), as_of=date(2026, 6, 1), regions=REGIONS
        )
        self.assert_held(compiled, "source_rent_cash_application_period_required")

    def test_api_and_body_age_mismatch_requires_review(self):
        compiled = compile_source_rent_cash(
            sample(sprtTrgtMaxAge="37"), as_of=date(2026, 6, 1), regions=REGIONS
        )
        self.assert_held(compiled, "source_rent_cash_source_change_requires_review")
        self.assertEqual(compiled.normalized["review_required_fields"], ["sprtTrgtMaxAge"])

    def test_single_source_identity_is_required(self):
        value = sample()
        value = replace(
            value,
            collection_sources=(
                *value.collection_sources,
                AnalysisSource("gov24", "2", "b" * 64),
            ),
        )
        compiled = compile_source_rent_cash(value, as_of=date(2026, 6, 1), regions=REGIONS)
        self.assert_held(compiled, "source_rent_cash_single_api_source_required")


if __name__ == "__main__":
    unittest.main()
