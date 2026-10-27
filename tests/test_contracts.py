"""Contract fixtures and cross-team invariants; no product API is implemented."""

import copy
import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from referencing import Registry, Resource


CONTRACTS = Path(__file__).resolve().parents[1] / "contracts"
FIXTURE_SCHEMAS = {
    "youth-benefit.json": "benefit.schema.json",
    "recommendation-request.json": "recommendation-request.schema.json",
    "recommendation-response.json": "recommendation-response.schema.json",
    "empty-response.json": "recommendation-response.schema.json",
    "fallback-response.json": "recommendation-response.schema.json",
    "simulation-request.json": "simulation-request.schema.json",
    "simulation-response.json": "simulation-response.schema.json",
    "meta.json": "meta.schema.json",
    "options.json": "options.schema.json",
    "sse-progress.json": "sse-progress.schema.json",
    "pv-unknown-response.json": "recommendation-response.schema.json",
    "error.json": "error.schema.json",
}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def references(value):
    if isinstance(value, dict):
        if "$ref" in value:
            yield value["$ref"]
        for child in value.values():
            yield from references(child)
    elif isinstance(value, list):
        for child in value:
            yield from references(child)


def military_range_ok(value):
    """Python-side invariant: min<=max when both are known (schema does not enforce)."""
    if value is None:
        return True
    low, high = value.get("min"), value.get("max")
    if low is None or high is None:
        return True
    return low <= high


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schemas = {
            path.name: read_json(path)
            for path in sorted((CONTRACTS / "v1").glob("*.schema.json"))
        }
        cls.registry = Registry().with_resources(
            (schema["$id"], Resource.from_contents(schema))
            for schema in cls.schemas.values()
        )

    def validator(self, name):
        return Draft202012Validator(
            self.schemas[name],
            registry=self.registry,
            format_checker=FormatChecker(),
        )

    def fixture(self, name):
        return read_json(CONTRACTS / "fixtures" / name)

    def test_schema_documents_and_local_references_are_valid(self):
        self.assertTrue(self.schemas, "No contract schemas found")
        self.assertEqual(
            len({schema["$id"] for schema in self.schemas.values()}),
            len(self.schemas),
        )
        for name, schema in self.schemas.items():
            with self.subTest(schema=name):
                Draft202012Validator.check_schema(schema)
                resolver = self.registry.resolver(base_uri=schema["$id"])
                for reference in references(schema):
                    resolver.lookup(reference)

    def test_every_fixture_matches_its_contract(self):
        fixtures = {path.name for path in (CONTRACTS / "fixtures").glob("*.json")}
        self.assertEqual(fixtures, set(FIXTURE_SCHEMAS))
        for fixture, schema in FIXTURE_SCHEMAS.items():
            with self.subTest(fixture=fixture):
                self.validator(schema).validate(self.fixture(fixture))

    def test_unknown_user_information_is_allowed(self):
        request = self.fixture("recommendation-request.json")
        for profile in ({}, {"age": None, "income_band": None}):
            with self.subTest(profile=profile):
                request["profile"] = profile
                self.validator("recommendation-request.schema.json").validate(request)

    def test_sentence_input_is_rejected(self):
        request = self.fixture("recommendation-request.json")
        self.assertNotIn("message", request)
        self.validator("recommendation-request.schema.json").validate(request)
        legacy = copy.deepcopy(request)
        legacy["message"] = "[legacy] sentence input is not part of the contract"
        with self.assertRaises(ValidationError):
            self.validator("recommendation-request.schema.json").validate(legacy)

    def test_period_option_lives_only_in_request_options(self):
        request = self.fixture("recommendation-request.json")
        self.assertNotIn("options", request["profile"].get("domain_data", {}))
        self.assertEqual(request["options"], {"period": "1y", "present_value": False})
        self.validator("recommendation-request.schema.json").validate(request)
        simulation = self.fixture("simulation-request.json")
        self.assertNotIn("options", simulation["profile"].get("domain_data", {}))
        self.assertEqual(simulation["options"], {"period": "1y", "present_value": False})
        self.validator("simulation-request.schema.json").validate(simulation)

    def test_youth_choice_fields_validate_only_for_youth(self):
        validator = self.validator("recommendation-request.schema.json")
        request = self.fixture("recommendation-request.json")
        validator.validate(request)
        bad = copy.deepcopy(request)
        bad["profile"]["employment_status"] = "not_a_status"
        with self.assertRaises(ValidationError):
            validator.validate(bad)
        bad = copy.deepcopy(request)
        bad["profile"]["domain_data"]["marital"] = "not_a_value"
        with self.assertRaises(ValidationError):
            validator.validate(bad)
        bad = copy.deepcopy(request)
        bad["profile"]["domain_data"]["unknown_future_field"] = "x"
        with self.assertRaises(ValidationError):
            validator.validate(bad)

    def test_military_range_shape_and_documented_limit(self):
        validator = self.validator("recommendation-request.schema.json")
        request = self.fixture("recommendation-request.json")
        cases = [
            ({"min": 0, "max": 0}, True),
            ({"min": 0, "max": 18}, True),
            ({"min": 19, "max": 24}, True),
            ({"min": 25, "max": None}, True),
            ({"min": 18, "max": 18}, True),
            (None, True),
        ]
        for value, _ in cases:
            with self.subTest(value=value):
                request["profile"]["domain_data"]["military_months_range"] = value
                validator.validate(request)
                self.assertTrue(military_range_ok(value))
        request["profile"]["domain_data"]["military_months_range"] = {"min": 24, "max": 18}
        validator.validate(request)
        self.assertFalse(
            military_range_ok({"min": 24, "max": 18}),
            "min<=max is an app-side invariant, not enforced by JSON Schema",
        )

    def test_special_none_and_parent_fields_shape(self):
        validator = self.validator("recommendation-request.schema.json")
        request = self.fixture("recommendation-request.json")
        request["profile"]["domain_data"]["special"] = ["none"]
        validator.validate(request)
        request["profile"]["domain_data"]["household"] = {
            "household_size": 2,
            "parent_income_band": "under_3m",
            "parent_asset_band": None,
        }
        validator.validate(request)
        bad = copy.deepcopy(request)
        bad["profile"]["domain_data"]["special"] = ["not_a_condition"]
        with self.assertRaises(ValidationError):
            validator.validate(bad)

    def test_confirmed_conditions_require_evidence_but_review_can_be_pending(self):
        response = self.fixture("recommendation-response.json")
        result = response["results"]["1y"]["pv_off"]["portfolios"][0]["policies"][0]["eligibility"][0]
        result["evidence"] = []
        validator = self.validator("recommendation-response.schema.json")
        for status in ("met", "not_met"):
            with self.subTest(status=status):
                result["status"] = status
                with self.assertRaises(ValidationError):
                    validator.validate(response)
        result["status"] = "needs_review"
        validator.validate(response)

    def test_legacy_need_check_status_is_rejected(self):
        response = self.fixture("recommendation-response.json")
        eligibility = response["results"]["1y"]["pv_off"]["portfolios"][0]["policies"][0]["eligibility"][0]
        eligibility["status"] = "need_check"
        with self.assertRaises(ValidationError):
            self.validator("recommendation-response.schema.json").validate(response)

    def test_result_follow_up_questions_are_rejected(self):
        response = self.fixture("recommendation-response.json")
        self.assertNotIn("follow_up_questions", response)
        self.validator("recommendation-response.schema.json").validate(response)
        for questions in (["extra question"], []):
            with self.subTest(questions=questions):
                bad = copy.deepcopy(response)
                bad["follow_up_questions"] = questions
                with self.assertRaises(ValidationError):
                    self.validator("recommendation-response.schema.json").validate(bad)

    def test_portfolio_combination_fixture_shape(self):
        response = self.fixture("recommendation-response.json")
        self.assertEqual(response["view"], "portfolio")
        for period in ("1y", "2y", "all"):
            for toggle in ("pv_off", "pv_on"):
                self.assertIn(toggle, response["results"][period])
        first = response["results"]["1y"]["pv_off"]
        self.assertLessEqual(len(first["portfolios"]), 3)
        top = first["portfolios"][0]
        self.assertIsInstance(top["total_won"], int)
        for policy in top["policies"]:
            for key in ("amount_in_period_won", "original_amount_won"):
                self.assertTrue(policy.get(key) is None or isinstance(policy[key], int))
        self.assertTrue(first["excluded"])
        self.assertTrue(first["not_eligible"])
        self.assertTrue(first["not_yet_reviewed"])
        self.assertTrue(first["closed_now"])
        self.assertTrue(top["given_up"])
        self.assertTrue(top["unverified_pairs"])
        self.validator("recommendation-response.schema.json").validate(response)

    def test_portfolio_fixture_totals_match_member_amounts_for_every_tab(self):
        for filename in ("recommendation-response.json", "pv-unknown-response.json"):
            response = self.fixture(filename)
            for period, modes in response["results"].items():
                for mode, result in modes.items():
                    for portfolio in result["portfolios"]:
                        with self.subTest(fixture=filename, period=period, mode=mode, rank=portfolio["rank"]):
                            amounts = [policy["amount_in_period_won"] for policy in portfolio["policies"]]
                            self.assertTrue(all(isinstance(amount, int) for amount in amounts))
                            self.assertEqual(portfolio["total_won"], sum(amounts))
                            self.assertEqual(portfolio["present_value_applied"], mode == "pv_on")
        response = self.fixture("recommendation-response.json")
        for period, total in (("1y", 5400000), ("2y", 6000000), ("all", 6000000)):
            with self.subTest(period=period):
                self.assertEqual(response["results"][period]["pv_off"]["portfolios"][0]["total_won"], total)

    def test_youth_mvp_interests_exclude_expansion_categories(self):
        request = self.fixture("recommendation-request.json")
        validator = self.validator("recommendation-request.schema.json")
        request["profile"]["domain_data"]["interests"] = ["employment", "housing"]
        validator.validate(request)
        for category in ("living", "asset", "education"):
            with self.subTest(category=category):
                bad = copy.deepcopy(request)
                bad["profile"]["domain_data"]["interests"].append(category)
                with self.assertRaises(ValidationError):
                    validator.validate(bad)
        # Storage's common contract stays extensible; active scope is enforced
        # by application policy filters when the product is implemented.
        benefit = self.fixture("youth-benefit.json")
        benefit["categories"] = ["example_future_category"]
        self.validator("benefit.schema.json").validate(benefit)

    def test_mvp_response_values_use_only_current_policy_cashflows(self):
        for filename in ("recommendation-response.json", "pv-unknown-response.json"):
            response = self.fixture(filename)
            for period, modes in response["results"].items():
                flows = {
                    "P1": [(m, 200000) for m in range(1, 13)],
                    "P3": [(m, 500000) for m in range(1, 7)],
                    "P6": [(12, 1500000)] if period == "1y" else [(12, 1500000), (24, 1500000)],
                }
                for mode, tab in modes.items():
                    for portfolio in tab["portfolios"]:
                        for policy in portfolio["policies"]:
                            pid = policy["policy_id"].split(":")[-1]
                            with self.subTest(fixture=filename, period=period, mode=mode, policy=pid):
                                self.assertIn(pid, flows)
                                raw = sum(amount for _, amount in flows[pid])
                                self.assertEqual(policy["original_amount_won"], raw)
                                if mode == "pv_on":
                                    amount = sum(
                                        Decimal(v) / Decimal("1.025") ** (Decimal(m) / 12)
                                        for m, v in flows[pid]
                                    )
                                    expected = int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
                                else:
                                    expected = raw
                                self.assertEqual(policy["amount_in_period_won"], expected)

    def test_mvp_simulation_and_single_policy_examples_match_current_scope(self):
        fallback = self.fixture("fallback-response.json")
        for period, amount in (("1y", 1500000), ("2y", 3000000), ("all", 3000000)):
            entry = fallback["results"][period]["pv_off"]["fallback_list"][0]
            self.assertEqual(entry["policy_id"], "youth:example:P6")
            self.assertEqual(entry["amount_in_period_won"], amount)
        simulation = self.fixture("simulation-response.json")
        self.assertEqual(simulation["before_total_won"], 2400000)
        self.assertEqual(simulation["after_total_won"], 1500000)
        gained = sum(p["amount_won"] for p in simulation["gained"])
        lost = sum(p["amount_won"] for p in simulation["lost"])
        self.assertEqual(simulation["after_total_won"] - simulation["before_total_won"], gained - lost)

    def test_fallback_and_empty_views(self):
        fallback = self.fixture("fallback-response.json")
        self.assertEqual(fallback["view"], "fallback_list")
        self.assertNotIn("fallback_list", fallback)
        for period in ("1y", "2y", "all"):
            off = fallback["results"][period]["pv_off"]
            self.assertEqual(off["calculation_status"], "available")
            self.assertIsNone(off["unavailable_reason"])
            self.assertEqual(off["portfolios"], [])
            self.assertTrue(off["fallback_list"])
            self.assertLessEqual(len(off["fallback_list"]), 5)
            on = fallback["results"][period]["pv_on"]
            self.assertEqual(on["calculation_status"], "unavailable")
            self.assertEqual(on["unavailable_reason"], "missing_discount_rate")
            self.assertEqual(on["portfolios"], [])
            self.assertIsNone(on["fallback_list"])
        self.assertIsNone(fallback["discount_rate"])
        self.assertEqual(fallback["discount_rate_status"], "unknown")
        empty = self.fixture("empty-response.json")
        self.assertEqual(empty["view"], "empty")
        self.assertEqual(empty["results"]["1y"]["pv_off"]["portfolios"], [])

    def test_request_limit_field_is_removed(self):
        request = self.fixture("recommendation-request.json")
        self.assertNotIn("limit", request)
        bad = copy.deepcopy(request)
        bad["limit"] = 5
        with self.assertRaises(ValidationError):
            self.validator("recommendation-request.schema.json").validate(bad)

    def test_region_code_is_single_source_of_truth(self):
        request = self.fixture("recommendation-request.json")
        self.assertEqual(request["profile"]["region_code"], "41110")
        self.assertEqual(request["profile"]["domain_data"]["region"], {"sido_code": "41"})
        bad = copy.deepcopy(request)
        bad["profile"]["domain_data"]["region"]["sigungu_code"] = "41110"
        with self.assertRaises(ValidationError):
            self.validator("recommendation-request.schema.json").validate(bad)

    def test_pv_unknown_branches_keep_raw_only(self):
        response = self.fixture("pv-unknown-response.json")
        self.assertIsNone(response["discount_rate"])
        self.assertEqual(response["discount_rate_status"], "unknown")
        raw = response["results"]["1y"]["pv_off"]
        self.assertEqual(raw["calculation_status"], "available")
        self.assertIsNone(raw["unavailable_reason"])
        self.assertTrue(raw["portfolios"])
        self.assertIsInstance(raw["portfolios"][0]["total_won"], int)
        for period in ("1y", "2y", "all"):
            tab = response["results"][period]["pv_on"]
            self.assertEqual(tab["calculation_status"], "unavailable")
            self.assertEqual(tab["unavailable_reason"], "missing_discount_rate")
            self.assertEqual(tab["portfolios"], [])
        validator = self.validator("recommendation-response.schema.json")
        validator.validate(response)
        bad = copy.deepcopy(response)
        bad["results"]["1y"]["pv_on"]["portfolios"] = raw["portfolios"]
        with self.assertRaises(ValidationError):
            validator.validate(bad)
        bad = copy.deepcopy(response)
        bad["results"]["1y"]["pv_off"]["unavailable_reason"] = "missing_discount_rate"
        with self.assertRaises(ValidationError):
            validator.validate(bad)

    def test_simulation_todo_requires_evidence_when_present(self):
        validator = self.validator("simulation-response.schema.json")
        response = self.fixture("simulation-response.json")
        self.assertIsNone(response["todo"])
        validator.validate(response)
        good = dict(copy.deepcopy(response))
        good["todo"] = {
            "text": "[가상] 지금 할 일",
            "evidence": [
                {
                    "quote": "[가상 예제] 지원 중단 조항",
                    "source": {
                        "url": "https://policy.example/youth/sample-p1",
                        "checked_at": "2026-10-26T09:00:00+09:00",
                        "document_version": "fictional-v1",
                    },
                }
            ],
        }
        validator.validate(good)
        for todo in ({"text": "[가상] 근거 없는 할 일"}, {"text": "x", "evidence": []}):
            with self.subTest(todo=todo):
                bad = copy.deepcopy(response)
                bad["todo"] = todo
                with self.assertRaises(ValidationError):
                    validator.validate(bad)

    def test_simulation_income_change_is_marked_as_assumption(self):
        response = self.fixture("simulation-response.json")
        note = response["income_change"]["assumption_note"]
        self.assertIn("가정", note)
        self.assertIn("확정", note)
        self.validator("simulation-response.schema.json").validate(response)

    def test_simulation_fixture_matches_effective_rate_definition(self):
        response = self.fixture("simulation-response.json")
        change = response["income_change"]
        self.assertIsNotNone(change)
        annual = response["annual"]
        expected = round(
            (annual["before_won"] - annual["after_won"])
            / (change["to_won"] - change["from_won"])
            * 100,
            1,
        )
        self.assertAlmostEqual(response["effective_marginal_rate"], expected)
        self.assertEqual(response["basis_period"], "1y")
        self.assertEqual(response["before_total_won"], annual["before_won"])
        self.assertEqual(response["after_total_won"], annual["after_won"])
        self.assertIn("연간", response["calculation_note"])
        self.assertIn("세금", response["calculation_note"])
        self.validator("simulation-response.schema.json").validate(response)
        self.validator("simulation-request.schema.json").validate(self.fixture("simulation-request.json"))

    def test_source_version_and_valid_time_are_required(self):
        benefit = self.fixture("youth-benefit.json")
        invalid_sources = []
        missing_version = copy.deepcopy(benefit["source"])
        del missing_version["document_version"]
        invalid_sources.append(missing_version)
        for timestamp in ("yesterday", "2026-10-26T09:00:00", "2026-02-30T09:00:00Z"):
            invalid_sources.append({**benefit["source"], "checked_at": timestamp})
        for source in invalid_sources:
            with self.subTest(source=source):
                benefit["source"] = source
                with self.assertRaises(ValidationError):
                    self.validator("benefit.schema.json").validate(benefit)

    def test_another_domain_can_use_the_common_contract(self):
        request = self.fixture("recommendation-request.json")
        request["domain"] = "example_future_domain"
        request["profile"] = {"domain_data": {"example_field": "fictional"}}
        self.validator("recommendation-request.schema.json").validate(request)
        response = self.fixture("recommendation-response.json")
        response["domain"] = request["domain"]
        policy = response["results"]["1y"]["pv_off"]["portfolios"][0]["policies"][0]
        policy["policy_id"] = "example_future_domain:example:001"
        self.validator("recommendation-response.schema.json").validate(response)

    def test_invalid_profile_types_and_unexpected_fields_are_rejected(self):
        request = self.fixture("recommendation-request.json")
        for age in (-1, "25", True):
            with self.subTest(age=age):
                request["profile"]["age"] = age
                with self.assertRaises(ValidationError):
                    self.validator("recommendation-request.schema.json").validate(request)
        request = self.fixture("recommendation-request.json")
        request["unexpected_field"] = "not part of the contract"
        with self.assertRaises(ValidationError):
            self.validator("recommendation-request.schema.json").validate(request)

    def test_new_error_codes_are_accepted(self):
        error = self.fixture("error.json")
        validator = self.validator("error.schema.json")
        validator.validate(error)
        for code in ("daily_limit_exceeded", "calculation_limit_exceeded"):
            with self.subTest(code=code):
                error["code"] = code
                validator.validate(error)

    def test_sensitive_paths_are_documented(self):
        readme = (CONTRACTS / "README.md").read_text(encoding="utf-8")
        for path in (
            "profile.domain_data.special",
            "profile.domain_data.household.parent_income_band",
            "profile.domain_data.household.parent_asset_band",
        ):
            self.assertIn(path, readme)


if __name__ == "__main__":
    unittest.main()
