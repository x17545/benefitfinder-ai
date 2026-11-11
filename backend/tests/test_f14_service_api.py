"""Test F14 across the actual HTTP boundary, independently of cash and F13."""

from copy import deepcopy
from dataclasses import replace
from datetime import date
import json
from pathlib import Path
import unittest
from typing import Any
from urllib.parse import quote

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from benefitfinder.application.service_catalog import MAX_SAFE_WON, ServiceCatalog, ServicePrice
from benefitfinder.bootstrap import create_app
from benefitfinder.infrastructure.example_services import example_services

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"


class F14ServiceApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schemas = {
            path.stem: json.loads(path.read_text()) for path in (CONTRACTS / "v1").glob("*.json")
        }
        cls.registry = Registry().with_resources(
            (schema["$id"], Resource.from_contents(schema)) for schema in cls.schemas.values()
        )

    def setUp(self) -> None:
        self.client = self.new_client()
        self.payload = json.loads((CONTRACTS / "fixtures/recommendation-request.json").read_text())

    def new_client(self, catalog: ServiceCatalog | None = None) -> TestClient:
        client = TestClient(create_app(service_catalog=catalog))
        self.addCleanup(client.close)
        return client

    def assert_contract(self, name: str, payload: dict[str, Any]) -> None:
        Draft202012Validator(
            self.schemas[name + ".schema"], registry=self.registry, format_checker=FormatChecker(),
        ).validate(payload)

    def recommend(self, client: TestClient, payload: dict[str, Any]) -> dict[str, Any]:
        response = client.post("/api/v1/recommendations", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assert_contract("recommendation-response", body)
        return body

    def detail(self, client: TestClient, policy_id: str) -> dict[str, Any]:
        response = client.get("/api/v1/benefits/" + quote(policy_id, safe=""))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assert_contract("benefit", body)
        return body

    def test_employment_and_housing_services_leave_all_cash_branches_unchanged(self) -> None:
        baseline = self.new_client(ServiceCatalog(()))
        for category, job, income, expected_cash, expected_total, priced_id, unknown_id in (
            ("employment", "job_seeker", "none", 5_400_000, 120_000, "S2", "S4"),
            ("housing", "employed", "between_15m_30m", 2_400_000, 250_000, "S1", "S3"),
        ):
            with self.subTest(category=category):
                payload = deepcopy(self.payload)
                payload["profile"].update(employment_status=job, income_band=income)
                payload["profile"]["domain_data"]["interests"] = [category]
                before = self.recommend(baseline, payload)
                after = self.recommend(self.client, payload)
                self.assertEqual(before["services"], [])
                self.assertIsNone(before["services_total_won"])
                self.assertEqual(after["results"], before["results"])
                self.assertEqual(after["view"], before["view"])
                self.assertEqual(after["ai_generated"], before["ai_generated"])
                self.assertEqual(
                    after["results"]["1y"]["pv_off"]["portfolios"][0]["total_won"], expected_cash,
                )
                self.assertEqual(after["services_total_won"], expected_total)
                self.assertTrue(after["virtual_data"])
                self.assertEqual(
                    [row["policy_id"] for row in after["services"]],
                    [f"youth:example:{key}" for key in (priced_id, unknown_id)],
                )
                self.assertEqual(after["services"][0]["service"]["estimated_value_won"], expected_total)
                unknown = after["services"][1]["service"]
                self.assertEqual(unknown["value_status"], "unknown")
                self.assertIsNone(unknown["estimated_value_won"])
                self.assertIsNone(unknown["unit_value_won"])
                for row in after["services"]:
                    detail = self.detail(self.client, row["policy_id"])
                    for key in ("source", "service", "application", "categories", "title"):
                        self.assertEqual(detail[key], row[key])
                    self.assertNotIn("eligibility", detail)
                    self.assertNotIn("profile", detail)
                    self.assertIsNone(detail.get("benefit_amount"))
                    self.assertTrue(all(condition["status"] == "met" for condition in row["eligibility"]))

    def test_empty_and_unknown_inputs_are_distinct_and_closed_stays_public(self) -> None:
        offers = example_services()
        closed = replace(offers[0], application={**offers[0].application, "status": "closed"})
        catalogue = self.new_client(ServiceCatalog((closed, *offers[1:])))
        profile = self.payload["profile"]
        profile.update(employment_status="employed", age=36)
        self.assertEqual(self.recommend(catalogue, self.payload)["services"], [])
        profile.update(employment_status=None, age=None)
        result = self.recommend(catalogue, self.payload)
        self.assertNotIn(closed.policy_id, {entry["policy_id"] for entry in result["services"]})
        self.assertEqual(self.detail(catalogue, closed.policy_id)["application"]["status"], "closed")
        self.assertTrue(all(
            any(condition["status"] == "needs_review" for condition in row["eligibility"])
            for row in result["services"]
        ))

    def test_incomplete_or_unreviewed_prices_never_become_money(self) -> None:
        offer = example_services()[1]
        assert offer.price is not None
        for price in (
            replace(offer.price, reviewed=False),
            replace(offer.price, url=None),
            replace(offer.price, url="https:///no-host"),
            replace(offer.price, checked_at=None),
            replace(offer.price, basis=" "),
            replace(offer.price, checked_at=date(2026, 11, 13)),
            replace(offer.price, unit_value_won=MAX_SAFE_WON),
        ):
            with self.subTest(price=price):
                client = self.new_client(ServiceCatalog(
                    (replace(offer, price=price),), today=lambda: date(2026, 11, 12),
                ))
                recommendation = self.recommend(client, self.payload)
                self.assertIsNone(recommendation["services_total_won"])
                service = recommendation["services"][0]["service"]
                self.assertEqual(service["value_status"], "unknown")
                for field in ("estimated_value_won", "unit_value_won", "value_source_url",
                              "value_checked_at", "value_basis"):
                    self.assertIsNone(service[field])
                self.assertEqual(self.detail(client, offer.policy_id)["service"], service)

    def test_recruitment_window_uses_inclusive_korean_dates_for_list_and_detail(self) -> None:
        offer = example_services()[0]
        today = date(2026, 11, 12)
        for label, starts, ends, expected in (
            ("마감 당일", "2026-11-10", "2026-11-12", "open"),
            ("마감 후", "2026-11-10", "2026-11-11", "closed"),
            ("시작 전", "2026-11-13", "2026-11-20", "upcoming"),
        ):
            with self.subTest(case=label):
                scheduled = replace(offer, application={
                    **offer.application, "starts_on": starts, "ends_on": ends,
                })
                client = self.new_client(ServiceCatalog((scheduled,), today=lambda: today))
                response = self.recommend(client, self.payload)
                if expected == "open":
                    self.assertEqual(response["services"][0]["policy_id"], offer.policy_id)
                    self.assertEqual(response["services_total_won"], 250_000)
                else:
                    self.assertEqual(response["services"], [])
                    self.assertIsNone(response["services_total_won"])
                self.assertEqual(self.detail(client, offer.policy_id)["application"]["status"], expected)

    def test_cash_policy_id_cannot_be_reused_as_service(self) -> None:
        colliding = replace(example_services()[0], policy_id="youth:example:P1")
        with self.assertRaisesRegex(ValueError, "cash_service_policy_id_collision"):
            create_app(service_catalog=ServiceCatalog((colliding,)))

    def test_total_overflow_is_not_rounded_or_used_as_cash(self) -> None:
        offer = example_services()[1]
        price = ServicePrice(MAX_SAFE_WON, "가상 기준", "https://policy.example/rate", date(2026, 11, 1), True)
        first = replace(offer, quantity=1, price=price)
        second = replace(first, policy_id="youth:example:S9")
        result = self.recommend(self.new_client(ServiceCatalog((first, second))), self.payload)
        self.assertEqual(len(result["services"]), 2)
        self.assertIsNone(result["services_total_won"])
        self.assertEqual(result["results"]["1y"]["pv_off"]["portfolios"][0]["total_won"], 5_400_000)
