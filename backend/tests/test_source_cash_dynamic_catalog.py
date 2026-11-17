"""Fictional source-cash catalog, evaluator, resolver, and calculation tests."""

from copy import deepcopy
from datetime import date
from functools import partial
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from backend.tests.catalog_fixtures import MemoryPolicies, release_data
from backend.tests.test_source_cash import REGIONS, sample
from benefitfinder.api.schemas.recommendation import UserProfile
from benefitfinder.application.published_recommendations import PublishedRecommendations
from benefitfinder.bootstrap import (
    _evaluate_published_condition,
    _resolve_published_payments,
)
from benefitfinder.domains.youth.published_eligibility import evaluate_condition
from benefitfinder.domains.youth.published_scenario import change_published_profile
from benefitfinder.domains.youth.source_cash import source_cash_payments
from benefitfinder.infrastructure.policy_catalog_records import CatalogRecord, validate_source_text
from benefitfinder.infrastructure.source_cash_records import SourceCashRule
from benefitfinder.ingestion.source_cash_dynamic import compile_source_cash_dynamic


TODAY = date(2026, 11, 17)
OTHER_POLICY_ID = "youth:fixture:other"


class SourceCashDynamicCatalogTest(unittest.TestCase):
    def compile(self, support: str, title: str) -> tuple[object, dict]:
        value = sample(plcyNm=title, plcySprtCn=support)
        result = compile_source_cash_dynamic(value, as_of=TODAY, regions=REGIONS)
        self.assertIsNotNone(result.payload, result.reasons)
        return value, deepcopy(result.payload)

    @staticmethod
    def source_only(payload: dict) -> dict:
        rule = next(rule for rule in payload["conditions"] if rule["kind"] == "source_cash")
        condition = next(
            condition
            for condition in payload["benefit"]["eligibility_conditions"]
            if condition["id"] == rule["condition_id"]
        )
        payload["conditions"] = [rule]
        payload["benefit"]["eligibility_conditions"] = [condition]
        return payload

    def expense_payload(self) -> tuple[object, dict]:
        value, payload = self.compile(
            "응시료 실제 납부액을 1인당 최대 10만원 이내 지원",
            "가상 청년 응시료 지원",
        )
        return value, self.source_only(payload)

    @staticmethod
    def profile(policy_id: str, *, payment_month: str = "2026-12", cost: int = 333_333):
        return UserProfile.model_validate(
            {
                "age": 25,
                "region_code": "11110",
                "employment_status": "job_seeker",
                "domain_data": {
                    "cash_facts": {
                        "source_cash": [
                            {
                                "policy_id": policy_id,
                                "payment_month": payment_month,
                                "eligible_cost_won": cost,
                                "prior_support_won": 0,
                                "other_reimbursement_won": 0,
                                "other_exclusions_clear": True,
                            }
                        ]
                    }
                },
            }
        )

    def test_compiled_fixed_and_expense_records_use_one_dynamic_rule(self):
        for support, title, mode in (
            ("면접수당 1회 5만원 지원 (연 최대 3회)", "가상 면접수당", "fixed"),
            (
                "응시료 실제 납부액을 1인당 최대 10만원 이내 지원",
                "가상 청년 응시료 지원",
                "expense",
            ),
        ):
            with self.subTest(mode=mode):
                value, payload = self.compile(support, title)
                record = CatalogRecord.model_validate(payload)
                validate_source_text(record, value.documents[0].text)
                self.assertIsNone(record.payments)
                rule = record.conditions[-1].source_cash
                self.assertIsNotNone(rule)
                assert rule is not None
                self.assertEqual(rule.policy_id, record.benefit.id)
                self.assertEqual(rule.mode, mode)
                self.assertEqual(record.benefit.benefit_amount.amount_won, rule.amount_won)
                self.assertEqual(record.benefit.benefit_amount.max_total_won, rule.amount_won)
                if mode == "expense":
                    self.assertIn("상한", record.benefit.benefit_amount.amount_text or "")

    def test_source_cash_rule_contract_rejects_missing_and_invalid_values(self):
        valid = {
            "policy_id": "youth:fixture:source-cash",
            "mode": "fixed",
            "amount_won": 100_000,
            "reimbursement_bps": 10000,
        }
        SourceCashRule.model_validate(valid)
        invalid = (
            {key: value for key, value in valid.items() if key != "policy_id"},
            {**valid, "policy_id": "other:fixture"},
            {**valid, "amount_won": 0},
            {**valid, "amount_won": 2**53},
            {**valid, "reimbursement_bps": 0},
            {**valid, "reimbursement_bps": 9999},
            {**valid, "mode": "expense", "reimbursement_bps": 10001},
            {**valid, "unknown": 1},
        )
        for candidate in invalid:
            with self.subTest(candidate=candidate), self.assertRaises(ValidationError):
                SourceCashRule.model_validate(candidate)

    def test_catalog_enforces_policy_identity_and_dynamic_metadata(self):
        _, payload = self.expense_payload()
        rule = payload["conditions"][-1]["source_cash"]

        changed = deepcopy(payload)
        changed["conditions"][-1]["source_cash"]["policy_id"] = OTHER_POLICY_ID
        with self.assertRaisesRegex(ValueError, "source_cash_policy_id_mismatch"):
            CatalogRecord.model_validate(changed)

        for payments in ((), [{"month": 1, "amount_won": rule["amount_won"]}]):
            with self.subTest(payments=payments):
                changed = deepcopy(payload)
                changed["payments"] = list(payments)
                with self.assertRaisesRegex(ValueError, "source_cash_requires_no_stored_payments"):
                    CatalogRecord.model_validate(changed)

        mutations = (
            ("type", "monthly"),
            ("pay_timing", "monthly_from_start"),
            ("period_months", 2),
            ("amount_won", rule["amount_won"] + 1),
            ("max_total_won", rule["amount_won"] + 1),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                changed = deepcopy(payload)
                changed["benefit"]["benefit_amount"][field] = value
                with self.assertRaisesRegex(
                    ValueError, "source_cash_requires_dynamic_amount_metadata"
                ):
                    CatalogRecord.model_validate(changed)

        changed = deepcopy(payload)
        changed["payment_evidence"] = []
        with self.assertRaisesRegex(ValueError, "reviewed_policy_requires_verified_calculation"):
            CatalogRecord.model_validate(changed)

        changed = deepcopy(payload)
        changed["benefit"]["benefit_amount"]["amount_text"] = "실제 적격 지출액"
        with self.assertRaisesRegex(ValueError, "expense_source_cash_requires_cap_label"):
            CatalogRecord.model_validate(changed)

        changed = deepcopy(payload)
        changed["maturity_accruals"] = [{"month": 1, "amount_won": rule["amount_won"]}]
        changed["maturity_accrual_evidence"] = deepcopy(changed["payment_evidence"])
        with self.assertRaises(ValueError):
            CatalogRecord.model_validate(changed)

    def test_duplicate_source_or_rent_dynamic_rules_are_rejected(self):
        _, payload = self.expense_payload()
        condition = deepcopy(payload["benefit"]["eligibility_conditions"][-1])
        condition["id"] = "source_cash_claim_2"
        rule = deepcopy(payload["conditions"][-1])
        rule["condition_id"] = "source_cash_claim_2"
        payload["benefit"]["eligibility_conditions"].append(condition)
        payload["conditions"].append(rule)
        with self.assertRaisesRegex(ValueError, "one_source_cash_rule_required"):
            CatalogRecord.model_validate(payload)

        _, payload = self.expense_payload()
        condition = deepcopy(payload["benefit"]["eligibility_conditions"][-1])
        condition["id"] = "rent_dynamic"
        payload["benefit"]["eligibility_conditions"].append(condition)
        payload["conditions"].append(
            {
                "condition_id": "rent_dynamic",
                "kind": "rent_support",
                "rent_support": {
                    "youth_income_max_bps": 6500,
                    "original_income_max_bps": 10500,
                    "youth_asset_max_won": 111_000_000,
                    "original_asset_max_won": 420_000_000,
                    "parent_exempt_min_age": 31,
                    "monthly_cap_won": 150_000,
                    "maximum_months": 12,
                    "support_starts_month": "2026-03",
                    "support_ends_month": "2027-12",
                },
            }
        )
        with self.assertRaisesRegex(ValueError, "source_cash_rent_dynamic_conflict"):
            CatalogRecord.model_validate(payload)

    def test_evaluator_uses_nested_policy_id_and_missing_facts_need_review(self):
        value, payload = self.expense_payload()
        policy = MemoryPolicies(payload).policies[0]
        rule = next(condition for condition in policy.conditions if condition["kind"] == "source_cash")

        missing = SimpleNamespace(domain_data=SimpleNamespace(cash_facts=None))
        self.assertEqual(evaluate_condition(missing, rule, today=TODAY), "needs_review")
        wrong = self.profile(OTHER_POLICY_ID)
        self.assertEqual(evaluate_condition(wrong, rule, today=TODAY), "needs_review")
        exact = self.profile(value.policy_id)
        self.assertEqual(evaluate_condition(exact, rule, today=TODAY), "met")
        with patch("benefitfinder.bootstrap.korea_today", return_value=TODAY):
            self.assertEqual(_evaluate_published_condition(exact, rule), "met")

    def test_published_calculation_uses_applicant_cost_and_preserves_cap(self):
        value, payload = self.expense_payload()
        payload["conditions"][-1]["source_cash"]["reimbursement_bps"] = 5000
        CatalogRecord.model_validate(payload)
        repository = MemoryPolicies(payload)
        service = PublishedRecommendations(
            repository,
            partial(evaluate_condition, today=TODAY),
            today=lambda: TODAY,
            resolve_payments=source_cash_payments,
        )
        request = SimpleNamespace(
            domain="youth",
            profile=self.profile(value.policy_id, cost=100_000),
        )
        result = service(request)

        self.assertEqual(result["eligibility"][value.policy_id]["status"], "met")
        self.assertEqual(result["catalog"]["payments"][value.policy_id], [(1, 50_000)])
        self.assertEqual(result["results"]["1y"]["pv_off"]["amounts"][value.policy_id], 50_000)

        request.profile = UserProfile.model_validate({"domain_data": {}})
        missing = service(request)
        self.assertEqual(missing["eligibility"][value.policy_id]["status"], "needs_review")
        self.assertNotIn(value.policy_id, missing["catalog"]["payments"])

    def test_bootstrap_resolver_preserves_source_and_static_payments(self):
        value, payload = self.expense_payload()
        payload["conditions"][-1]["source_cash"]["reimbursement_bps"] = 5000
        source_policy = MemoryPolicies(payload).policies[0]
        self.assertEqual(
            _resolve_published_payments(
                self.profile(value.policy_id, cost=100_000), source_policy, TODAY
            ),
            ((1, 50_000),),
        )

        static_policy = MemoryPolicies(release_data()).policies[0]
        self.assertEqual(
            _resolve_published_payments(
                UserProfile.model_validate({"domain_data": {}}),
                static_policy,
                TODAY,
            ),
            static_policy.payments,
        )

    def test_scenario_does_not_reuse_exclusions_from_original_profile(self):
        value, payload = self.expense_payload()
        policy = MemoryPolicies(payload).policies[0]
        original = self.profile(value.policy_id)
        for scenario in ("income_up", "employed", "independent", "age_plus_one"):
            with self.subTest(scenario=scenario):
                before, after, _, _ = change_published_profile(
                    original, scenario, None, rules=policy.conditions
                )
                self.assertEqual(source_cash_payments(before, policy, TODAY), ((1, 100_000),))
                self.assertIsNone(source_cash_payments(after, policy, TODAY))
                self.assertTrue(original.domain_data.cash_facts.source_cash[0].other_exclusions_clear)


if __name__ == "__main__":
    unittest.main()
