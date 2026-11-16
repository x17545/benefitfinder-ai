"""월세지원 입력의 Pydantic과 공용 JSON Schema 정합을 가상값으로 검증한다."""

import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from benefitfinder.api.schemas.cash_facts import CashFacts, RentSupportFacts


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "contracts" / "v1" / "youth-profile.schema.json"
MAX_WON = 2**53 - 1
FIELD_NAMES = {
    "youth_income_ratio_bps",
    "original_income_ratio_bps",
    "youth_assets_won",
    "original_assets_won",
    "monthly_rent_won",
    "housing_benefit_rent_won",
    "original_household_exempt",
    "subscription_account",
    "independent_from_parents",
    "other_exclusions_clear",
    "requested_months",
    "prior_supported_months",
    "first_payment_month",
}
COMPLETE = {
    "youth_income_ratio_bps": 6500,
    "original_income_ratio_bps": 10500,
    "youth_assets_won": 111_000_000,
    "original_assets_won": 420_000_000,
    "monthly_rent_won": 300_000,
    "housing_benefit_rent_won": 50_000,
    "original_household_exempt": False,
    "subscription_account": True,
    "independent_from_parents": True,
    "other_exclusions_clear": True,
    "requested_months": 12,
    "prior_supported_months": 0,
    "first_payment_month": "2026-12",
}
INTEGER_FIELDS = {
    "youth_income_ratio_bps",
    "original_income_ratio_bps",
    "youth_assets_won",
    "original_assets_won",
    "monthly_rent_won",
    "housing_benefit_rent_won",
    "requested_months",
    "prior_supported_months",
}
BOOLEAN_FIELDS = {
    "original_household_exempt",
    "subscription_account",
    "independent_from_parents",
    "other_exclusions_clear",
}
BOUNDARIES = {
    "youth_income_ratio_bps": ((0, True), (1_000_000, True), (-1, False), (1_000_001, False)),
    "original_income_ratio_bps": (
        (0, True),
        (1_000_000, True),
        (-1, False),
        (1_000_001, False),
    ),
    "youth_assets_won": ((0, True), (MAX_WON, True), (-1, False), (MAX_WON + 1, False)),
    "original_assets_won": ((0, True), (MAX_WON, True), (-1, False), (MAX_WON + 1, False)),
    "monthly_rent_won": ((0, True), (MAX_WON, True), (-1, False), (MAX_WON + 1, False)),
    "housing_benefit_rent_won": (
        (0, True),
        (MAX_WON, True),
        (-1, False),
        (MAX_WON + 1, False),
    ),
    "requested_months": ((1, True), (24, True), (0, False), (25, False)),
    "prior_supported_months": ((0, True), (24, True), (-1, False), (25, False)),
}


class RentSupportProfileContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(cls.schema)
        cls.validator = Draft202012Validator(cls.schema, format_checker=FormatChecker())
        cls.schema_fields = cls.schema["$defs"]["cash_facts"]["properties"]["rent_support"][
            "properties"
        ]

    def schema_accepts(self, facts: object) -> bool:
        profile = {"domain_data": {"cash_facts": {"rent_support": facts}}}
        return self.validator.is_valid(profile)

    @staticmethod
    def pydantic_accepts(facts: object) -> bool:
        try:
            CashFacts.model_validate({"rent_support": facts})
        except ValidationError:
            return False
        return True

    def assert_same_validation(self, facts: object, expected: bool) -> None:
        self.assertEqual(self.pydantic_accepts(facts), expected, facts)
        self.assertEqual(self.schema_accepts(facts), expected, facts)

    def test_fields_are_optional_and_models_stay_in_sync(self) -> None:
        self.assertEqual(set(RentSupportFacts.model_fields), FIELD_NAMES)
        self.assertEqual(set(self.schema_fields), FIELD_NAMES)
        self.assertEqual(RentSupportFacts().model_dump(), dict.fromkeys(FIELD_NAMES))
        self.assertIsNone(CashFacts().rent_support)

        for omitted in FIELD_NAMES:
            facts = dict(COMPLETE)
            del facts[omitted]
            with self.subTest(omitted=omitted):
                self.assert_same_validation(facts, True)

    def test_missing_and_null_are_unknown_and_preserved(self) -> None:
        for facts in ({}, None, dict.fromkeys(FIELD_NAMES)):
            with self.subTest(facts=facts):
                self.assert_same_validation(facts, True)
        parsed = CashFacts.model_validate({"rent_support": {}}).rent_support
        self.assertEqual(parsed, RentSupportFacts())
        self.assertEqual(parsed.model_dump(), dict.fromkeys(FIELD_NAMES))

    def test_complete_payload_and_month_pattern_match(self) -> None:
        self.assert_same_validation(COMPLETE, True)
        self.assertEqual(RentSupportFacts.model_validate(COMPLETE).model_dump(), COMPLETE)
        for month in ("2024-03", "2026-12"):
            with self.subTest(month=month):
                self.assert_same_validation({"first_payment_month": month}, True)
        for month in ("2024-3", "2024-00", "2026-13", 202603):
            with self.subTest(month=month):
                self.assert_same_validation({"first_payment_month": month}, False)

    def test_numeric_boundaries_match(self) -> None:
        for field, cases in BOUNDARIES.items():
            for value, expected in cases:
                with self.subTest(field=field, value=value):
                    self.assert_same_validation({field: value}, expected)

    def test_integer_fields_reject_booleans(self) -> None:
        for field in INTEGER_FIELDS:
            for value in (False, True):
                with self.subTest(field=field, value=value):
                    self.assert_same_validation({field: value}, False)

    def test_boolean_fields_reject_integer_codes(self) -> None:
        for field in BOOLEAN_FIELDS:
            for value in (0, 1):
                with self.subTest(field=field, value=value):
                    self.assert_same_validation({field: value}, False)

    def test_extra_fields_are_rejected(self) -> None:
        self.assert_same_validation({"unexpected": None}, False)


if __name__ == "__main__":
    unittest.main()
