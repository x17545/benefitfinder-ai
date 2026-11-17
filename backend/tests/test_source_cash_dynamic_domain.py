"""Fictional contract and domain tests for applicant-specific source cash."""

from datetime import date
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from jsonschema import Draft202012Validator
from pydantic import ValidationError

from benefitfinder.api.schemas.cash_facts import CashFacts, SourceCashFacts
from benefitfinder.domains.youth.source_cash import (
    assess_source_cash,
    source_cash_payments,
)


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "contracts" / "v1" / "youth-profile.schema.json"
POLICY_ID = "youth:fixture:source-cash"
OTHER_POLICY_ID = "youth:fixture:other"
TODAY = date(2026, 11, 15)
FACT_FIELDS = {
    "policy_id",
    "payment_month",
    "eligible_cost_won",
    "prior_support_won",
    "other_reimbursement_won",
    "other_exclusions_clear",
}
CHECK_NAMES = {
    "policy_id",
    "payment_month",
    "eligible_cost",
    "prior_support",
    "other_reimbursement",
    "other_exclusions_clear",
    "amount",
}
FIXED_RULE = {
    "policy_id": POLICY_ID,
    "mode": "fixed",
    "amount_won": 200_000,
    "reimbursement_bps": 10000,
}
EXPENSE_RULE = {
    "policy_id": POLICY_ID,
    "mode": "expense",
    "amount_won": 200_000,
    "reimbursement_bps": 5000,
}


def source_fact(**changes):
    values = {
        "policy_id": POLICY_ID,
        "payment_month": "2026-11",
        "prior_support_won": 0,
        "other_reimbursement_won": 0,
        "other_exclusions_clear": True,
    }
    values.update(changes)
    return SourceCashFacts(**values)


def profile(*facts):
    cash_facts = CashFacts(source_cash=list(facts))
    return SimpleNamespace(domain_data=SimpleNamespace(cash_facts=cash_facts))


def policy(*conditions, payments=None):
    return SimpleNamespace(
        policy_id=POLICY_ID,
        conditions=conditions,
        payments=payments,
    )


def source_condition(rule):
    return {"condition_id": "source_cash", "kind": "source_cash", "source_cash": rule}


class SourceCashContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(cls.schema)
        cls.validator = Draft202012Validator(cls.schema)
        cls.schema_item = cls.schema["$defs"]["cash_facts"]["properties"]["source_cash"]["items"]

    def schema_accepts(self, facts):
        profile_data = {"domain_data": {"cash_facts": {"source_cash": facts}}}
        return self.validator.is_valid(profile_data)

    @staticmethod
    def pydantic_accepts(facts):
        try:
            CashFacts.model_validate({"source_cash": facts})
        except ValidationError:
            return False
        return True

    def test_contract_fields_default_to_unknown(self):
        self.assertEqual(set(self.schema_item["properties"]), FACT_FIELDS)
        self.assertEqual(set(SourceCashFacts.model_fields), FACT_FIELDS)
        self.assertEqual(self.schema_item["required"], ["policy_id"])
        parsed = SourceCashFacts(policy_id=POLICY_ID)
        self.assertEqual(
            parsed.model_dump(),
            {
                "policy_id": POLICY_ID,
                "payment_month": None,
                "eligible_cost_won": None,
                "prior_support_won": None,
                "other_reimbursement_won": None,
                "other_exclusions_clear": None,
            },
        )
        self.assertIsNone(CashFacts().source_cash)
        self.assertEqual(CashFacts(source_cash=[]).source_cash, [])

    def test_policy_ids_are_scoped_and_limited(self):
        valid = {"policy_id": "youth:" + "A" * 100}
        self.assertTrue(self.pydantic_accepts([valid]))
        self.assertTrue(self.schema_accepts([valid]))
        for invalid in (
            {"policy_id": "other:fixture"},
            {"policy_id": "youth:" + "A" * 101},
            {"policy_id": "youth:"},
        ):
            with self.subTest(invalid=invalid):
                self.assertFalse(self.pydantic_accepts([invalid]))
                self.assertFalse(self.schema_accepts([invalid]))

    def test_duplicate_policy_ids_are_rejected(self):
        duplicate = [
            {"policy_id": POLICY_ID},
            {"policy_id": POLICY_ID, "payment_month": "2026-12"},
        ]
        with self.assertRaisesRegex(ValidationError, "duplicate_source_cash_policy_id"):
            CashFacts.model_validate({"source_cash": duplicate})

    def test_list_limit_and_strict_scalar_types(self):
        complete = [{"policy_id": f"youth:fixture:{index}"} for index in range(100)]
        self.assertTrue(self.pydantic_accepts(complete))
        self.assertTrue(self.schema_accepts(complete))
        too_many = complete + [{"policy_id": "youth:fixture:extra"}]
        self.assertFalse(self.pydantic_accepts(too_many))
        self.assertFalse(self.schema_accepts(too_many))
        for field in (
            "eligible_cost_won",
            "prior_support_won",
            "other_reimbursement_won",
        ):
            self.assertFalse(self.pydantic_accepts([{"policy_id": POLICY_ID, field: True}]))
        self.assertFalse(
            self.pydantic_accepts(
                [
                    {
                        "policy_id": POLICY_ID,
                        "other_exclusions_clear": 1,
                    }
                ]
            )
        )

    def test_explicit_zero_and_null_are_distinct(self):
        explicit = SourceCashFacts(
            policy_id=POLICY_ID,
            eligible_cost_won=0,
            prior_support_won=0,
            other_reimbursement_won=0,
            other_exclusions_clear=False,
        )
        unknown = SourceCashFacts(policy_id=POLICY_ID)
        self.assertEqual(explicit.eligible_cost_won, 0)
        self.assertEqual(explicit.prior_support_won, 0)
        self.assertIs(unknown.eligible_cost_won, None)
        self.assertIs(unknown.prior_support_won, None)


class SourceCashDomainTest(unittest.TestCase):
    def test_missing_facts_and_wrong_policy_are_needs_review(self):
        for candidate_profile in (
            SimpleNamespace(domain_data=SimpleNamespace(cash_facts=None)),
            profile(),
            profile(source_fact(policy_id=OTHER_POLICY_ID)),
        ):
            with self.subTest(candidate_profile=candidate_profile):
                statuses = assess_source_cash(candidate_profile, POLICY_ID, FIXED_RULE, TODAY)
                self.assertEqual(set(statuses), CHECK_NAMES)
                self.assertEqual(set(statuses.values()), {"needs_review"})
                self.assertIsNone(
                    source_cash_payments(
                        candidate_profile,
                        policy(source_condition(FIXED_RULE)),
                        TODAY,
                    )
                )

    def test_absent_and_explicit_zero_have_different_statuses(self):
        absent = source_fact(
            prior_support_won=None,
            other_reimbursement_won=None,
            other_exclusions_clear=None,
        )
        statuses = assess_source_cash(profile(absent), POLICY_ID, FIXED_RULE, TODAY)
        self.assertEqual(statuses["prior_support"], "needs_review")
        self.assertEqual(statuses["other_reimbursement"], "needs_review")
        self.assertEqual(statuses["other_exclusions_clear"], "needs_review")
        explicit = source_fact()
        statuses = assess_source_cash(profile(explicit), POLICY_ID, FIXED_RULE, TODAY)
        self.assertEqual(statuses["prior_support"], "met")
        self.assertEqual(statuses["other_reimbursement"], "met")
        self.assertEqual(statuses["other_exclusions_clear"], "met")
        nonzero = source_fact(
            prior_support_won=1,
            other_reimbursement_won=1,
        )
        statuses = assess_source_cash(profile(nonzero), POLICY_ID, FIXED_RULE, TODAY)
        self.assertEqual(statuses["prior_support"], "not_met")
        self.assertEqual(statuses["other_reimbursement"], "not_met")

    def test_negative_and_false_facts_are_not_met(self):
        negative = SimpleNamespace(
            policy_id=POLICY_ID,
            payment_month="2026-11",
            eligible_cost_won=-1,
            prior_support_won=-1,
            other_reimbursement_won=-1,
            other_exclusions_clear=False,
        )
        candidate_profile = SimpleNamespace(
            domain_data=SimpleNamespace(cash_facts=SimpleNamespace(source_cash=[negative]))
        )
        statuses = assess_source_cash(candidate_profile, POLICY_ID, EXPENSE_RULE, TODAY)
        self.assertEqual(statuses["eligible_cost"], "not_met")
        self.assertEqual(statuses["prior_support"], "not_met")
        self.assertEqual(statuses["other_reimbursement"], "not_met")
        self.assertEqual(statuses["other_exclusions_clear"], "not_met")
        fixed_statuses = assess_source_cash(candidate_profile, POLICY_ID, FIXED_RULE, TODAY)
        self.assertEqual(fixed_statuses["eligible_cost"], "not_met")

    def test_payment_month_calendar_and_cross_year_offset(self):
        for month, expected, offset in (
            ("2026-10", "not_met", None),
            ("2026-11", "met", 0),
            ("2027-01", "met", 2),
            ("2126-11", "met", 1200),
            ("2126-12", "not_met", None),
        ):
            with self.subTest(month=month):
                candidate_profile = profile(source_fact(payment_month=month))
                statuses = assess_source_cash(
                    candidate_profile,
                    POLICY_ID,
                    FIXED_RULE,
                    TODAY,
                )
                self.assertEqual(statuses["payment_month"], expected)
                payments = source_cash_payments(
                    candidate_profile,
                    policy(source_condition(FIXED_RULE)),
                    TODAY,
                )
                if expected == "met":
                    self.assertEqual(payments, ((offset, 200_000),))
                else:
                    self.assertIsNone(payments)

    def test_expense_reimbursement_math_and_cap(self):
        for cost, expected in (
            (333_333, 166_666),
            (500_000, 200_000),
            (1_000_000, 200_000),
        ):
            with self.subTest(cost=cost):
                payments = source_cash_payments(
                    profile(source_fact(eligible_cost_won=cost)),
                    policy(source_condition(EXPENSE_RULE)),
                    TODAY,
                )
                self.assertEqual(payments, ((0, expected),))

    def test_expense_missing_cost_never_becomes_the_cap(self):
        candidate = profile(source_fact(eligible_cost_won=None))
        statuses = assess_source_cash(candidate, POLICY_ID, EXPENSE_RULE, TODAY)
        self.assertEqual(statuses["eligible_cost"], "needs_review")
        self.assertIsNone(
            source_cash_payments(
                candidate,
                policy(source_condition(EXPENSE_RULE)),
                TODAY,
            )
        )
        fixed = source_cash_payments(
            candidate,
            policy(source_condition(FIXED_RULE)),
            TODAY,
        )
        self.assertEqual(fixed, ((0, 200_000),))

    def test_resolver_uses_only_one_source_rule_and_exact_policy_fact(self):
        manual = {"condition_id": "manual", "kind": "manual"}
        candidate_profile = profile(
            source_fact(eligible_cost_won=300_000),
            source_fact(
                policy_id=OTHER_POLICY_ID,
                eligible_cost_won=900_000,
            ),
        )
        resolved = source_cash_payments(
            candidate_profile,
            policy(manual, source_condition(EXPENSE_RULE)),
            TODAY,
        )
        self.assertEqual(resolved, ((0, 150_000),))
        duplicate = source_cash_payments(
            candidate_profile,
            policy(
                source_condition(FIXED_RULE),
                source_condition(EXPENSE_RULE),
            ),
            TODAY,
        )
        self.assertIsNone(duplicate)

    def test_rule_policy_id_must_match_assessment_and_policy(self):
        mismatched_rule = {
            **FIXED_RULE,
            "policy_id": OTHER_POLICY_ID,
        }
        statuses = assess_source_cash(
            profile(source_fact()),
            POLICY_ID,
            mismatched_rule,
            TODAY,
        )
        self.assertEqual(set(statuses), CHECK_NAMES)
        self.assertEqual(set(statuses.values()), {"needs_review"})
        self.assertIsNone(
            source_cash_payments(
                profile(source_fact()),
                policy(source_condition(mismatched_rule)),
                TODAY,
            )
        )
        missing_rule_id = {key: value for key, value in FIXED_RULE.items() if key != "policy_id"}
        self.assertIsNone(
            source_cash_payments(
                profile(source_fact()),
                policy(source_condition(missing_rule_id)),
                TODAY,
            )
        )

    def test_static_payments_are_the_only_fallback(self):
        static = ((1, 100_000), (2, 100_000))
        actual = source_cash_payments(
            profile(source_fact()),
            policy(
                {"condition_id": "manual", "kind": "manual"},
                payments=static,
            ),
            TODAY,
        )
        self.assertEqual(actual, static)
        self.assertIsNone(
            source_cash_payments(
                profile(),
                policy(
                    {"condition_id": "source_cash", "kind": "source_cash"},
                    payments=static,
                ),
                TODAY,
            )
        )


if __name__ == "__main__":
    unittest.main()
