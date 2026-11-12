"""Published records, conservative eligibility, and actual HTTP catalogue wiring."""

from __future__ import annotations

import json
import unittest
from dataclasses import asdict
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError
from referencing import Registry, Resource

from backend.tests.catalog_fixtures import MemoryPolicies, SOURCE_TEXT, release_data
from benefitfinder.api.schemas.recommendation import UserProfile
from benefitfinder.application.published_policies import PolicyCatalogUnavailable
from benefitfinder.application.published_recommendations import PublishedRecommendations
from benefitfinder.bootstrap import create_app
from benefitfinder.domains.youth.published_eligibility import evaluate_condition
from benefitfinder.infrastructure.policy_catalog_records import (
    CatalogRecord,
    record_hash,
    validate_source_text,
)
from benefitfinder.domain.portfolio import CalculationLimitExceeded

ROOT = Path(__file__).resolve().parents[2]


def validator(name: str):
    schemas = [
        json.loads(path.read_text()) for path in (ROOT / "contracts/v1").glob("*.schema.json")
    ]
    registry = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in schemas)
    schema = json.loads((ROOT / "contracts/v1" / name).read_text())
    return Draft202012Validator(schema, registry=registry, format_checker=FormatChecker())


class PublishedRecordTest(unittest.TestCase):
    def test_round_trip_and_exact_original_hash_and_quotes(self):
        record = CatalogRecord.model_validate(release_data())
        restored = CatalogRecord.model_validate_json(record.model_dump_json())
        self.assertEqual(record_hash(record), record_hash(restored))
        validate_source_text(record, SOURCE_TEXT)
        with self.assertRaisesRegex(ValueError, "source_hash_mismatch"):
            validate_source_text(record, SOURCE_TEXT + "변경")
        altered = release_data()
        altered["benefit"]["eligibility_conditions"][0]["evidence"][0]["quote"] = "없는 문구"
        with self.assertRaisesRegex(ValueError, "quote_missing_from_source"):
            validate_source_text(CatalogRecord.model_validate(altered), SOURCE_TEXT)

    def test_invalid_review_amount_version_rule_and_source_are_rejected(self):
        mutations = [
            lambda d: d.update(unresolved_combination_rules=True),
            lambda d: d.update(payments=None),
            lambda d: d["payments"][0].update(amount_won=999),
            lambda d: d["benefit"]["source"].update(url="https://user:secret@catalog.example"),
            lambda d: d["conditions"][0].update(kind="unsupported_formula"),
            lambda d: d["conditions"][0].update(field="domain_data.special"),
            lambda d: d["benefit"]["eligibility_conditions"][0]["evidence"][0]["source"].update(
                document_version="other"
            ),
            lambda d: d["benefit"]["benefit_amount"].update(type="maturity"),
            lambda d: d["benefit"].update(categories=["education"]),
            lambda d: d["conditions"][0].update(maximum=True),
            lambda d: d["benefit"].update(lifetime_once=True),
            lambda d: d["benefit"]["benefit_amount"].update(pay_timing="monthly_from_start"),
            lambda d: d["benefit"]["benefit_amount"].update(pay_timing="yearly"),
        ]
        for mutate in mutations:
            data = release_data()
            mutate(data)
            with self.subTest(mutate=mutate), self.assertRaises((ValidationError, ValueError)):
                CatalogRecord.model_validate(data)

    def test_income_boundaries_and_missing_information(self):
        rule = {"kind": "income_max", "maximum": 30_000_000, "inclusive": True}
        profile = SimpleNamespace(income_band="between_30m_50m", income_won=None)
        self.assertEqual(evaluate_condition(profile, rule), "not_met")
        profile.income_band = "between_15m_30m"
        self.assertEqual(evaluate_condition(profile, rule), "met")
        self.assertEqual(evaluate_condition(profile, {**rule, "inclusive": False}), "needs_review")
        profile.income_won = 30_000_000
        self.assertEqual(evaluate_condition(profile, {**rule, "inclusive": False}), "not_met")
        profile.income_won = None
        profile.income_band = "unknown"
        self.assertEqual(evaluate_condition(profile, rule), "needs_review")

    def test_military_interval_is_never_replaced_with_its_upper_bound(self):
        rule = {"kind": "age", "minimum": 19, "maximum": 34, "military_extension_years": 3}
        profile = SimpleNamespace(
            age=36,
            domain_data=SimpleNamespace(
                military_months_range=SimpleNamespace(min=0, max=24),
            ),
        )
        self.assertEqual(evaluate_condition(profile, rule), "needs_review")
        profile.domain_data.military_months_range = SimpleNamespace(min=25, max=None)
        self.assertEqual(evaluate_condition(profile, rule), "met")
        profile.age = 37
        self.assertEqual(evaluate_condition(profile, rule), "needs_review")
        profile.age = 38
        self.assertEqual(evaluate_condition(profile, rule), "not_met")

    def test_selection_special_missing_and_explicit_none(self):
        rule = {"kind": "selection", "field": "domain_data.special", "values": ["disabled"]}
        profile = SimpleNamespace(domain_data=SimpleNamespace(special=None))
        self.assertEqual(evaluate_condition(profile, rule), "needs_review")
        profile.domain_data.special = ["none"]
        self.assertEqual(evaluate_condition(profile, rule), "not_met")
        profile.domain_data.special = ["disabled"]
        self.assertEqual(evaluate_condition(profile, rule), "met")


class PublishedCatalogApiTest(unittest.TestCase):
    def setUp(self):
        self.payload = json.loads(
            (ROOT / "contracts/fixtures/recommendation-request.json").read_text()
        )
        self.payload["profile"]["age"] = 25
        self.payload["profile"]["domain_data"]["interests"] = ["housing"]

    def recommend(self, records):
        repository = MemoryPolicies(*records)
        client = TestClient(create_app(policy_repository=repository))
        self.addCleanup(client.close)
        response = client.post("/api/v1/recommendations", json=self.payload)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        validator("recommendation-response.schema.json").validate(body)
        return body, client, repository

    def test_published_ids_amounts_diagnosis_and_detail_use_same_source(self):
        data, client, _ = self.recommend([release_data("A"), release_data("B", amount=2_000_000)])
        first = data["results"]["1y"]["pv_off"]["portfolios"][0]
        self.assertEqual(first["total_won"], 3_000_000)
        self.assertEqual(len(first["unverified_pairs"]), 1)
        self.assertIsNone(data["discount_rate"])
        self.assertEqual(data["results"]["1y"]["pv_on"]["calculation_status"], "unavailable")
        self.assertEqual(data["services"], [])
        for policy in first["policies"]:
            self.assertEqual(policy["eligibility"][0]["status"], "met")
            detail = client.get("/api/v1/benefits/" + policy["policy_id"]).json()
            validator("benefit.schema.json").validate(detail)
            self.assertEqual(detail["source"], policy["source"])
        self.assertEqual(client.get("/api/v1/benefits/youth:example:P1").status_code, 404)

    def test_fallback_empty_and_closed_unknown_unreviewed_are_distinct(self):
        closed, unknown, unreviewed, failed = [release_data(key) for key in ("C", "U", "R", "F")]
        closed["benefit"]["application"]["ends_on"] = "2000-01-01"
        unknown["benefit"]["application"]["status"] = "unknown"
        unreviewed["benefit"]["reviewed"] = False
        unreviewed["payments"] = None
        failed["conditions"][0]["minimum"] = 30
        data, _, _ = self.recommend([release_data(), closed, unknown, unreviewed, failed])
        view = data["results"]["1y"]["pv_off"]
        self.assertEqual(data["view"], "fallback_list")
        self.assertEqual([p["policy_id"] for p in view["fallback_list"]], ["youth:catalog:A"])
        self.assertEqual([p["policy_id"] for p in view["closed_now"]], ["youth:catalog:C"])
        self.assertEqual(len(view["not_yet_reviewed"]), 2)
        self.assertEqual(view["not_eligible"][0]["failed"], ["age"])
        empty, _, _ = self.recommend([])
        self.assertEqual(empty["view"], "empty")

    def test_confirmed_conflicts_are_symmetric_and_all_clash_uses_individual_list(self):
        a, b = release_data("A"), release_data("B")
        a["benefit"]["conflicts"] = [
            {"policy_id": "youth:catalog:B", "evidence": a["payment_evidence"]}
        ]
        data, _, _ = self.recommend([a, b])
        self.assertEqual(data["view"], "fallback_list")
        self.assertEqual(len(data["results"]["1y"]["pv_off"]["fallback_list"]), 2)

    def test_opportunity_cost_and_why_have_confirmed_evidence(self):
        a, b, c = [release_data(key) for key in ("A", "B", "C")]
        a["benefit"]["conflicts"] = [
            {"policy_id": "youth:catalog:B", "evidence": a["payment_evidence"]}
        ]
        data, _, _ = self.recommend([a, b, c])
        view = data["results"]["1y"]["pv_off"]
        self.assertEqual(view["excluded"][0]["policy_id"], "youth:catalog:B")
        self.assertTrue(view["excluded"][0]["evidence"])
        self.assertEqual(view["why"]["deciding_conflict"], ["youth:catalog:A", "youth:catalog:B"])
        self.assertEqual(view["why"]["gap_won"], 0)

    def test_search_limit_and_deadline_return_explicit_error_not_partial_success(self):
        repository = MemoryPolicies(release_data("A"), release_data("B"))
        with TestClient(create_app(policy_repository=repository)) as client:
            with patch(
                "benefitfinder.application.published_recommendations.calculate_portfolios",
                side_effect=CalculationLimitExceeded("private-internal-message"),
            ):
                response = client.post("/api/v1/recommendations", json=self.payload)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["code"], "calculation_limit_exceeded")
            self.assertNotIn("private-internal-message", response.text)
            with patch(
                "benefitfinder.application.published_recommendations.monotonic", side_effect=[0, 46]
            ):
                response = client.post("/api/v1/recommendations", json=self.payload)
            self.assertEqual(response.json()["code"], "calculation_limit_exceeded")

    def test_sensitive_verdicts_never_enter_explanation_context_even_with_general_id(self):
        data = release_data()
        data["conditions"] = [
            {
                "condition_id": "age",
                "kind": "selection",
                "field": "domain_data.special",
                "values": ["disabled"],
            }
        ]
        repository = MemoryPolicies(data, release_data("B"))
        explanations = AsyncMock()
        explanations.search.return_value = ()
        self.payload["profile"]["domain_data"]["special"] = ["disabled"]
        with TestClient(
            create_app(policy_repository=repository, explanation_repository=explanations)
        ) as client:
            response = client.post("/api/v1/recommendations", json=self.payload)
            self.assertEqual(response.status_code, 200)
        self.assertTrue(explanations.search.call_count)
        for call in explanations.search.call_args_list:
            context = call.args[0]
            self.assertEqual(context.conditions, ())
            self.assertNotIn("disabled", repr(asdict(context)))

    def test_catalog_failure_returns_safe_503_without_fictional_fallback(self):
        repository = MemoryPolicies()
        repository.list_published = lambda: (_ for _ in ()).throw(
            PolicyCatalogUnavailable("private-secret")
        )
        with TestClient(
            create_app(policy_repository=repository), raise_server_exceptions=False
        ) as client:
            response = client.post("/api/v1/recommendations", json=self.payload)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "dependency_unavailable")
        self.assertNotIn("private-secret", response.text)

    def test_simulation_uses_one_snapshot_and_published_income_boundary(self):
        a, b = release_data("A"), release_data("B")
        a["benefit"]["eligibility_conditions"][0]["id"] = "income"
        a["benefit"]["eligibility_conditions"][0]["description"] = "연 소득 상한"
        a["benefit"]["eligibility_conditions"][0]["evidence"][0]["quote"] = "연 소득 3천만 원 이하."
        a["conditions"] = [
            {
                "condition_id": "income",
                "kind": "income_max",
                "maximum": 30_000_000,
                "inclusive": True,
            }
        ]
        self.payload["profile"]["income_band"] = "between_15m_30m"
        repository = MemoryPolicies(a, b)
        with TestClient(create_app(policy_repository=repository)) as client:
            response = client.post(
                "/api/v1/simulations", json={**self.payload, "scenario": "income_up"}
            )
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            validator("simulation-response.schema.json").validate(body)
            self.assertEqual(repository.reads, 1)
            self.assertEqual(body["income_change"]["from_won"], 30_000_000)
            self.assertEqual(body["income_change"]["to_won"], 30_000_001)
            self.assertEqual(body["lost"][0]["policy_id"], "youth:catalog:A")
            self.assertEqual(body["before_total_won"], 2_000_000)
            self.assertEqual(body["after_total_won"], 1_000_000)
            pv = client.post(
                "/api/v1/simulations",
                json={**self.payload, "scenario": "income_up", "options": {"present_value": True}},
            )
            self.assertEqual(pv.status_code, 503)

    def test_interests_and_military_uncertainty_do_not_use_fixture_ids(self):
        a = release_data()
        a["conditions"][0]["military_extension_years"] = 3
        repository = MemoryPolicies(a)
        service = PublishedRecommendations(
            repository, evaluate_condition, today=lambda: date(2026, 11, 12)
        )
        profile = UserProfile.model_validate(
            {
                "age": 36,
                "domain_data": {
                    "interests": ["housing"],
                    "military_months_range": {"min": 0, "max": 24},
                },
            }
        )
        raw = service(SimpleNamespace(domain="youth", profile=profile))
        self.assertEqual(raw["eligibility"]["youth:catalog:A"]["status"], "needs_review")
        self.assertTrue(raw["results"]["1y"]["pv_off"]["portfolios"])
        profile.domain_data.interests = ["employment"]
        self.assertEqual(
            service(SimpleNamespace(domain="youth", profile=profile))["eligibility"], {}
        )


if __name__ == "__main__":
    unittest.main()
