"""Source compiler tests use fictional notices, with no model or DB calls."""

from datetime import date
import unittest

from benefitfinder.infrastructure.policy_catalog_records import CatalogRecord, validate_source_text
from benefitfinder.ingestion.source_cash_dynamic import compile_source_cash_dynamic
from backend.tests.test_source_cash import REGIONS, sample


class SourceCashDynamicTest(unittest.TestCase):
    def compile(self, support, **changes):
        value = sample(plcyNm="가상 청년 응시료 지원", plcySprtCn=support, **changes)
        return value, compile_source_cash_dynamic(value, as_of=date(2026, 11, 17), regions=REGIONS)

    def test_expense_has_cap_metadata_and_no_invented_payment_schedule(self):
        value, result = self.compile("응시료 실제 납부액을 1인당 최대 10만원 이내 지원")
        self.assertIsNotNone(result.payload, result.reasons)
        record = CatalogRecord.model_validate(result.payload)
        validate_source_text(record, value.documents[0].text)
        self.assertIsNone(record.payments)
        self.assertEqual(record.conditions[-1].source_cash.mode, "expense")
        self.assertEqual(record.benefit.benefit_amount.max_total_won, 100000)
        self.assertIn("상한", record.benefit.benefit_amount.amount_text)
        self.assertEqual(result.normalized["ai_review_calls"], 0)

    def test_fixed_interview_counts_one_claim_without_annual_multiplier(self):
        value = sample(plcyNm="가상 면접수당", plcySprtCn="면접수당 1회 5만원 지원 (연 최대 3회)")
        result = compile_source_cash_dynamic(value, as_of=date(2026, 11, 17), regions=REGIONS)
        self.assertIsNotNone(result.payload, result.reasons)
        record = CatalogRecord.model_validate(result.payload)
        self.assertEqual(record.conditions[-1].source_cash.mode, "fixed")
        self.assertEqual(record.benefit.benefit_amount.max_total_won, 50000)
        self.assertIsNone(record.payments)

    def test_cap_without_actual_cost_semantics_is_not_a_fixed_grant(self):
        for support in ("정착 지원금 최대 50만원 지원", "정착 지원금 50만원 이내 지원"):
            with self.subTest(support=support):
                _, result = self.compile(support)
                self.assertIsNone(result.payload)

    def test_compound_costs_different_caps_and_non_full_reimbursement_hold(self):
        for support in (
            "응시료 최대 10만원 및 사진 촬영비 최대 3만원 실비 지원",
            "응시료의 50%를 최대 10만원 지원",
            "응시료 50~100%를 최대 10만원 지원",
            "가구 소득 300만원 이하, 응시료 최대 10만원 지원",
        ):
            with self.subTest(support=support):
                _, result = self.compile(support)
                self.assertIsNone(result.payload)

    def test_wages_employer_subsidies_and_user_costs_are_not_grants(self):
        for support in (
            "기업 인건비 50만원 지원", "생활임금 50만원 지급", "인턴 1인당 460만원 지원",
            "응시료 대출 10만원 지원", "기숙사비 10만원 부담",
            "1개사당 지원금 40,000천원 지원", "1개소 당 4,000만원 지원",
            "1인당 20만원 상당의 수강권 지원",
        ):
            with self.subTest(support=support):
                _, result = self.compile(support)
                self.assertIsNone(result.payload)

    def test_monthly_and_split_grants_need_a_different_calculator(self):
        for support in ("월 50만원을 6개월 동안 지원", "지원금 100만원을 2회 분할 지급",
                        "영농정착 지원금 (800천원*12개월)"):
            with self.subTest(support=support):
                _, result = self.compile(support)
                self.assertIsNone(result.payload)

    def test_unmapped_overlap_and_region_are_not_silently_removed(self):
        for changes, expected in (
            ({"ptcpPrpTrgtCn": "유사사업과 중복 수급 불가"}, "source_dynamic_overlap_requires_mapping"),
            ({"zipCd": "99999"}, "source_dynamic_residence_not_executable"),
        ):
            with self.subTest(changes=changes):
                _, result = self.compile("응시료 실제 납부액 최대 10만원 지원", **changes)
                self.assertIsNone(result.payload)
                self.assertEqual(result.reasons, (expected,))

    def test_all_eligibility_terms_and_source_versions_remain_bound(self):
        value, result = self.compile("응시료 실제 납부액 최대 10만원 지원",
                                    addAplyQlfcCndCn="가상 기관의 인정 시험만 신청 가능")
        record = CatalogRecord.model_validate(result.payload)
        validate_source_text(record, value.documents[0].text)
        self.assertIn("인정 시험", " ".join(c.description for c in record.benefit.eligibility_conditions))
        self.assertTrue(record.benefit.source.document_version.endswith(":source-facts-v1"))

    def test_mixed_categories_do_not_expand_mvp_scope(self):
        for changes in ({"lclsfNm": "일자리,교육"}, {"mclsfNm": "취업,창업"}):
            with self.subTest(changes=changes):
                _, result = self.compile("응시료 실비 최대 10만원 지원", **changes)
                self.assertIsNone(result.payload)
                self.assertEqual(result.reasons, ("source_dynamic_outside_mvp",))
