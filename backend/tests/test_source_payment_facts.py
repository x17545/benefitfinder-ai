"""Deterministic payment parsing tests use virtual source excerpts only."""

from __future__ import annotations

import unittest

from benefitfinder.ingestion.source_payment_facts import (
    PaymentFacts,
    PaymentParseResult,
    parse_source_payment,
)


class SourcePaymentFactsTest(unittest.TestCase):
    def assert_payment(self, text: str) -> tuple[PaymentParseResult, PaymentFacts]:
        result = parse_source_payment(text)
        self.assertIsInstance(result, PaymentParseResult)
        self.assertIsNotNone(result.amountfact)
        facts = result.amountfact
        assert facts is not None
        for start, end in facts.evidence_spans:
            self.assertGreaterEqual(start, 0)
            self.assertGreater(end, start)
            self.assertLessEqual(end, len(text))
        return result, facts

    def test_exact_monthly_amount_and_period_create_relative_schedule(self):
        text = "가상 지원금 월 50만원씩 6개월간 매월 지급. 신청일 기준 익월부터 지급"
        result, facts = self.assert_payment(text)

        self.assertEqual(facts.amount_won, 500_000)
        self.assertEqual(facts.amount_type, "monthly")
        self.assertEqual(facts.period_months, 6)
        self.assertEqual(facts.pay_timing, "monthly_from_start")
        self.assertEqual(facts.payments, tuple((month, 500_000) for month in range(1, 7)))
        self.assertIn("monthly_payment_start_confirmed", facts.reasons)
        self.assertIn("확정 6개월분", facts.amount_text)
        self.assertIn("신청 기준 다음 달부터", facts.amount_text)
        self.assertTrue(any("신청일 기준 익월부터" in text[slice(*span)] for span in facts.evidence_spans))
        self.assertIsNone(facts.lifetime_once)
        self.assertEqual(result.reasons, facts.reasons)

    def test_maximum_period_keeps_only_one_fixed_month(self):
        text = "가상 월세 지원은 월 50만원, 최대 6개월 지급"
        _, facts = self.assert_payment(text)

        self.assertEqual(facts.amount_won, 500_000)
        self.assertEqual(facts.period_months, 1)
        self.assertEqual(facts.payments, ())
        self.assertIn("전체 기간 합계가 아닌 1개월분", facts.amount_text)
        self.assertIn("maximum_period_not_used_as_total", facts.reasons)

    def test_individual_worker_allowance_is_not_rejected_by_a_company_quota(self):
        _, facts = self.assert_payment(
            "가상 기업당 최대 1명, 대체근로자에게 월 30만원씩 최대 10개월 지원"
        )

        self.assertEqual(facts.period_months, 1)
        self.assertEqual(facts.payments, ())

    def test_fixed_monthly_amount_without_period_keeps_one_month(self):
        _, facts = self.assert_payment("가상 주거안정 지원금 월 20만원 지급")

        self.assertEqual(facts.period_months, 1)
        self.assertEqual(facts.payments, ())
        self.assertIn("support_period_not_stated", facts.reasons)

    def test_eligibility_month_is_not_mistaken_for_support_period(self):
        text = "가상 신청자는 창업 후 3개월이 경과한 자, 1인당 월 30만원 최대 360만원 지원"
        _, facts = self.assert_payment(text)

        self.assertEqual(facts.period_months, 1)
        self.assertEqual(facts.payments, ())

    def test_monthly_amount_does_not_imply_the_first_payment_month(self):
        for timing in (
            "",
            "선정일 기준 익월부터 지급",
            "신청일 기준 익월부터 심사",
            "신청일 기준 2개월 뒤부터 지급",
            "신청일 기준 익월부터 지급하지 않음",
            "신청일 기준 익월부터 지급. 예외 대상은 당월 지급",
        ):
            with self.subTest(timing=timing):
                _, facts = self.assert_payment(f"가상 지원금 월 50만원씩 6개월간 매월 지급. {timing}")
                self.assertEqual(facts.amount_won, 500_000)
                self.assertEqual(facts.payments, ())
                self.assertIn("payment_start_unknown_or_unsupported", facts.reasons)

    def test_exact_next_month_payment_uses_relative_month_one(self):
        text = "가상 지원금 100만원 □ 지급일 : 접수일 기준 다음달 20일까지"
        _, facts = self.assert_payment(text)

        self.assertEqual(facts.amount_type, "lump_sum")
        self.assertEqual(facts.pay_timing, "once")
        self.assertEqual(facts.payments, ((1, 1_000_000),))
        self.assertIn("actual_calendar_schedule_unknown", facts.reasons)
        amount_span = facts.evidence_spans[0]
        self.assertIn("100만원", text[slice(*amount_span)])

    def test_selection_announcement_month_is_not_a_payment_schedule(self):
        text = (
            "가상 면접수당 5만원 지원 "
            "□ 후보자 발표: 신청일 기준 익월 10일 안내 "
            "□ 면접수당 지급: 신청일 기준 일월 20일 입금"
        )
        result = parse_source_payment(text)

        self.assertIsNone(result.amountfact)
        self.assertIn("one_time_payment_timing_unknown", result.reasons)

    def test_current_month_and_exact_month_delay_are_supported(self):
        cases = {
            "가상 지원금 10만원 신청월 지급": 0,
            "가상 지원금 10만원 신청 후 2개월 뒤 지급": 2,
            "가상 지원금 10만원 접수 후 3개월 후 입금": 3,
        }
        for text, expected_month in cases.items():
            with self.subTest(text=text):
                _, facts = self.assert_payment(text)
                self.assertEqual(facts.payments, ((expected_month, 100_000),))

    def test_one_time_amount_without_an_explicit_month_is_held(self):
        cases = (
            "가상 면접비용 1회당 5만원 지원",
            "가상 일시금 10만원 지급",
            "가상 지원금 10만원 1회 지급",
            "가상 지원금 10만원 이수 시 지급",
            "가상 지원금 10만원 선정일 기준 다음 달 지급",
            "가상 지원금 10만원 다음 달 지급",
        )
        for text in cases:
            with self.subTest(text=text):
                result = parse_source_payment(text)
                self.assertIsNone(result.amountfact)
                self.assertTrue(result.reasons)

    def test_day_based_delay_is_not_rounded_to_a_month(self):
        result = parse_source_payment("가상 지원금 10만원 신청 후 30일 이내 지급")

        self.assertIsNone(result.amountfact)
        self.assertIn("payment_timing_not_integer_month", result.reasons)

    def test_annual_cap_counts_one_eligible_payment(self):
        text = "가상 면접수당 회당 5만원 지원, 연 최대 3회, 접수일 기준 다음 달 지급"
        _, facts = self.assert_payment(text)

        self.assertEqual(facts.payments, ((1, 50_000),))
        self.assertIn("annual_limit_not_multiplied", facts.reasons)

    def test_exact_annual_amount_and_duration_do_not_supply_calendar(self):
        _, facts = self.assert_payment("가상 정착지원금 연 100만원씩 2년간 지원")

        self.assertEqual(facts.amount_type, "lump_sum")
        self.assertEqual(facts.pay_timing, "yearly")
        self.assertEqual(facts.period_months, 24)
        self.assertEqual(facts.payments, ())

    def test_maximum_annual_period_keeps_one_year_component(self):
        _, facts = self.assert_payment("가상 정착지원금 연 100만원, 최대 2년 지원")

        self.assertEqual(facts.period_months, 12)
        self.assertEqual(facts.payments, ())
        self.assertIn("전체 기간 합계가 아닌 1년분", facts.amount_text)

    def test_explicit_lifetime_limit_is_preserved(self):
        _, facts = self.assert_payment("가상 주거비 월 20만원, 12개월, 생애 1회")

        self.assertIs(facts.lifetime_once, True)
        self.assertIn("lifetime_once_explicit", facts.reasons)

    def test_business_once_is_not_inferred_as_a_lifetime_limit(self):
        _, facts = self.assert_payment("가상 주거비 월 20만원, 12개월, 사업 1인 1회")

        self.assertIsNone(facts.lifetime_once)

    def test_fixed_allowance_paid_by_regional_currency_is_still_cash_like(self):
        text = (
            "가상 면접수당 회당 5만원 지원(지역화폐 충전지급), "
            "접수일 기준 다음 달 20일까지 지급"
        )
        _, facts = self.assert_payment(text)

        self.assertEqual(facts.amount_won, 50_000)
        self.assertEqual(facts.payments, ((1, 50_000),))
        self.assertIn("5만원", facts.amount_text)

    def test_service_grants_and_split_totals_are_held(self):
        cases = (
            "가상 교육서비스 바우처 100만원 지원",
            "가상 정장 대여 10만원 지원",
            "가상 지역상품권 지원금 100만원 지급(2회 분할)",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertIsNone(parse_source_payment(text).amountfact)

    def test_quarterly_payment_savings_and_user_costs_are_not_monthly_grants(self):
        cases = (
            "가상 주거비 월 10만원, 최대 3년, 분기별 지급",
            "가상 청약통장 월 100만원 납입한도 지원",
            "가상 기숙사비 월 15만원, 주거 지원",
            "가상 지원내용 월 215만원 수준(세전)",
            "가상 월세 지원 1인 월 20만원씩 최대 24개월\n- 실제 납부하는 월세의 최대 20만원",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertIsNone(parse_source_payment(text).amountfact)

    def test_caps_actual_cost_differentials_wages_financing_and_employer_support_are_held(self):
        cases = (
            "가상 지원금 최대 50만원 지원",
            "가상 월세 지원 월 50만원 이내",
            "가상 교육비는 소득에 따라 50만원부터 100만원까지 차등 지원",
            "가상 급여조건 월 200만원 이상 지급",
            "가상 기업 인건비 월 50만원 지원",
            "가상 대출원금 최대 1억원 지원",
            "가상 지원금 응시료 실비 지원",
            "가상 상시근로자 3인 이상 중소기업에 군민 신규채용 시 1인당 월 70만원씩 12개월 지급",
            "가상 (중소기업) 인턴 2개월간 월 150만원씩 2회 고용지원금 지급",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertIsNone(parse_source_payment(text).amountfact)

    def test_parenthesized_limit_is_a_cap_even_when_limits_are_otherwise_removed(self):
        cases = (
            "가상 지원금액: 1인당 10만원(한도, 횟수제한 없음)",
            "가상 지원금액: 1인당 10만원 ( 한도 )",
        )
        for text in cases:
            with self.subTest(text=text):
                result = parse_source_payment(text)
                self.assertIsNone(result.amountfact)
                self.assertIn("cash_amount_not_fixed", result.reasons)

    def test_multiple_exact_amounts_are_not_guessed(self):
        result = parse_source_payment("가상 지원금 50만원 또는 100만원 지원")

        self.assertIsNone(result.amountfact)
        self.assertIn("cash_amount_ambiguous", result.reasons)

    def test_payment_timing_uses_all_mentions_and_ignores_announcement(self):
        _, facts = self.assert_payment(
            "가상 면접수당 5만원 지원\n선정 결과 다음달 안내\n지급일: 신청일 기준 다음달 20일"
        )
        self.assertEqual(facts.payments, ((1, 50_000),))
        result = parse_source_payment(
            "가상 수당 5만원 지급\n1차 지급은 신청월\n2차 지급은 신청일 기준 다음달"
        )
        self.assertIsNone(result.amountfact)

    def test_separate_half_year_payment_clause_prevents_monthly_schedule(self):
        result = parse_source_payment(
            "가상 지원 내용: 월 15만원, 최대 12개월\n"
            "대상자는 만 19세부터 39세이며 소득 기준에 해당해야 합니다.\n"
            "지급 방법: 반기별 신청·지급"
        )
        self.assertIsNone(result.amountfact)
        self.assertIn("payment_frequency_not_monthly_or_once", result.reasons)

    def test_empty_text_is_an_explicit_failure(self):
        for text in ("", "   "):
            with self.subTest(text=repr(text)):
                result = parse_source_payment(text)
                self.assertEqual(result, PaymentParseResult(None, ("payment_text_empty",)))


if __name__ == "__main__":
    unittest.main()
