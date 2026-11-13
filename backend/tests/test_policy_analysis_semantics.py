"""Fictional monetary-role regressions; no entitlement or arithmetic inference."""

from unittest import TestCase

from benefitfinder.ai.policy_analysis_semantics import benefit_amounts, evidence_kind


class EvidenceSemanticsTests(TestCase):
    def test_cap_kept_own_cost_threshold_principal_and_budget_excluded(self):
        quote = ('최대 47만원 지원\n본인부담금 3천원 제외 금액 지급\n'
                 '주택 거래금액 2억원 이하\n연 소득 800만원 이하\n'
                 '대출 원금 4천만원\n사업 예산 7억원')
        self.assertEqual(benefit_amounts(quote), (470000,))

    def test_monthly_range_not_multiplied_by_duration(self):
        self.assertEqual(benefit_amounts('I유형: 이행시 월 73~117만원, 7개월 지원'),
                         (730000, 1170000))

    def test_income_support_is_not_an_income_threshold(self):
        self.assertEqual(benefit_amounts('소득지원(I유형): 구직촉진수당 월 73만원'),
                         (730000,))

    def test_burden_following_value_stays_context_only(self):
        self.assertEqual(benefit_amounts('2천원의 본인부담금 제외'), ())

    def test_unpriced_service_has_no_assumed_zero_value(self):
        self.assertEqual(benefit_amounts('가상 면접 정장 무료 대여 2회'), ())

    def test_application_deadline_is_not_a_payment_month(self):
        self.assertEqual(evidence_kind('[detailDeadline]\n4월 중', 'timing', 2, 2),
                         'application')
