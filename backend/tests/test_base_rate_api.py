"""One persisted rate per analysis, shared with public metadata."""

from datetime import date, datetime, timezone
import json
from pathlib import Path
import time
import unittest

from fastapi.testclient import TestClient

from backend.tests.catalog_fixtures import MemoryPolicies, release_data
from benefitfinder.application.base_rates import BaseRateObservation, BaseRateUnavailable
from benefitfinder.bootstrap import create_app

ROOT = Path(__file__).resolve().parents[2]


class Rates:
    def __init__(self, rate: float | None = 0.03) -> None:
        self.value = rate
        self.reads = 0

    def latest(self) -> BaseRateObservation | None:
        self.reads += 1
        return None if self.value is None else BaseRateObservation(
            self.value, date(2026, 11, 2), datetime(2026, 11, 3, tzinfo=timezone.utc),
        )


class BaseRateApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = json.loads((ROOT / "contracts/fixtures/recommendation-request.json").read_text())
        self.payload["profile"]["age"] = 25
        self.payload["profile"]["domain_data"]["interests"] = ["housing"]
        self.policies = MemoryPolicies(release_data("A"), release_data("B", amount=2_000_000))

    def test_recommendations_meta_and_simulation_use_the_same_persisted_rate(self) -> None:
        rates = Rates()
        with TestClient(create_app(policy_repository=self.policies, base_rate_repository=rates)) as client:
            body = client.post("/api/v1/recommendations", json=self.payload).json()
            self.assertEqual(rates.reads, 1)
            self.assertEqual(body["discount_rate"], 0.03)
            self.assertEqual(body["discount_rate_status"], "verified")
            nominal = body["results"]["1y"]["pv_off"]["portfolios"][0]["total_won"]
            pv = body["results"]["1y"]["pv_on"]["portfolios"][0]["total_won"]
            self.assertEqual(nominal, 3_000_000)
            self.assertLess(pv, nominal)
            meta = client.get("/api/v1/meta").json()
            self.assertEqual(meta["base_rate"], 0.03)
            self.assertEqual(meta["base_rate_status"], "verified")
            before_simulation_reads = rates.reads
            response = client.post("/api/v1/simulations", json={
                "domain": "youth", "profile": self.payload["profile"], "scenario": "age_plus_one",
                "options": {"period": "1y", "present_value": True},
            })
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["before_total_won"], pv)
            self.assertEqual(response.json()["after_total_won"], pv)
            self.assertEqual(rates.reads, before_simulation_reads + 1)
            rates.value = 0.12
            changed = client.post("/api/v1/recommendations", json=self.payload).json()
            self.assertEqual(changed["discount_rate"], 0.12)
            self.assertLess(changed["results"]["1y"]["pv_on"]["portfolios"][0]["total_won"], pv)

    def test_missing_rate_or_storage_failure_preserves_nominal_and_unknown_pv(self) -> None:
        class BrokenRates:
            def latest(self):
                raise BaseRateUnavailable("PRIVATE password")

        for rates in (Rates(None), BrokenRates()):
            with self.subTest(rates=type(rates).__name__), TestClient(create_app(
                policy_repository=self.policies, base_rate_repository=rates,
            )) as client:
                response = client.post("/api/v1/recommendations", json=self.payload)
                self.assertEqual(response.status_code, 200)
                body = response.json()
                self.assertIsNone(body["discount_rate"])
                self.assertEqual(body["discount_rate_status"], "unknown")
                self.assertEqual(body["results"]["1y"]["pv_off"]["portfolios"][0]["total_won"], 3_000_000)
                self.assertEqual(body["results"]["1y"]["pv_on"]["unavailable_reason"], "missing_discount_rate")
                self.assertIsNone(client.get("/api/v1/meta").json()["base_rate"])
                self.assertNotIn("PRIVATE", response.text)

    def test_fictional_development_mode_does_not_label_actual_rate_as_sample(self) -> None:
        rates = Rates(0.05)
        with TestClient(create_app(base_rate_repository=rates)) as client:
            meta = client.get("/api/v1/meta").json()
            self.assertTrue(meta["virtual_data"])
            self.assertEqual(meta["base_rate_status"], "unknown")
        self.assertEqual(rates.reads, 0)

    def test_rate_read_time_is_part_of_the_same_analysis_budget(self) -> None:
        class SlowRates:
            def latest(self):
                time.sleep(0.04)
                return None

        app = create_app(policy_repository=self.policies, base_rate_repository=SlowRates(),
                         analysis_timeout_seconds=0.01)
        with TestClient(app) as client:
            response = client.post("/api/v1/recommendations", json=self.payload)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["code"], "calculation_limit_exceeded")

    def test_ambiguous_rate_repository_configuration_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "ambiguous_base_rate_repository"):
            create_app(base_rate_repository=Rates(), base_rate_database_url="fictional")
