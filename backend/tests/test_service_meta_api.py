"""Public metadata reflects the published snapshot and never consumes analysis."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
import unittest

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker

from backend.tests.catalog_fixtures import MemoryPolicies, release_data
from benefitfinder.application.analysis_usage import AnalysisUsageUnavailable
from benefitfinder.application.published_policies import PolicyCatalogUnavailable
from benefitfinder.application.response_mapper import MVP_DATA_UPDATED_AT
from benefitfinder.bootstrap import create_app
from benefitfinder.infrastructure.memory_analysis_usage import MemoryAnalysisUsage

ROOT = Path(__file__).resolve().parents[2]


class ServiceMetaApiTest(unittest.TestCase):
    def validate(self, body: dict, schema: str = "meta") -> None:
        Draft202012Validator(
            json.loads((ROOT / f"contracts/v1/{schema}.schema.json").read_text()),
            format_checker=FormatChecker(),
        ).validate(body)

    def test_development_metadata_is_explicitly_virtual_and_matches_recommendations(self) -> None:
        usage = MemoryAnalysisUsage()
        app = create_app(analysis_usage=usage)
        payload = json.loads((ROOT / "contracts/fixtures/recommendation-request.json").read_text())
        with TestClient(app) as client:
            for _ in range(2):
                response = client.get("/api/v1/meta")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["cache-control"], "no-store")
                body = response.json()
                self.validate(body)
                self.assertTrue(body["virtual_data"])
                self.assertEqual(datetime.fromisoformat(body["data_updated_at"]),
                                 datetime.fromisoformat(MVP_DATA_UPDATED_AT))
                self.assertEqual(body["daily_remaining"], 100)
            recommendation = client.post("/api/v1/recommendations", json=payload).json()
            meta = client.get("/api/v1/meta").json()
            self.assertEqual(meta["daily_remaining"], 99)
            self.assertEqual(meta["base_rate"], recommendation["discount_rate"])
            self.assertEqual(meta["base_rate_status"], recommendation["discount_rate_status"])

    def test_latest_published_time_uses_instants_and_refreshes_without_restarting(self) -> None:
        repository = MemoryPolicies(release_data("A"), release_data("B"))
        a, b = repository.policies
        repository.policies = (
            replace(a, published_at="2026-11-02T10:00:00+09:00"),
            replace(b, published_at="2026-11-02T02:00:00Z"),
        )
        app = create_app(policy_repository=repository)
        with TestClient(app) as client:
            body = client.get("/api/v1/meta").json()
            self.validate(body)
            self.assertEqual(datetime.fromisoformat(body["data_updated_at"]),
                             datetime.fromisoformat("2026-11-02T02:00:00Z"))
            self.assertFalse(body["virtual_data"])
            self.assertIsNone(body["base_rate"])
            self.assertEqual(body["base_rate_status"], "unknown")
            repository.policies = (replace(a, published_at="2026-11-03T00:00:00Z"),)
            self.assertTrue(client.get("/api/v1/meta").json()["data_updated_at"].startswith("2026-11-03"))
        self.assertEqual(repository.reads, 2)

    def test_empty_or_invalid_timestamp_does_not_invent_a_successful_update(self) -> None:
        repository = MemoryPolicies(release_data())
        policy = repository.policies[0]
        with TestClient(create_app(policy_repository=repository)) as client:
            for timestamp in (None, "PRIVATE invalid time", "2026-11-02T00:00:00"):
                repository.policies = () if timestamp is None else (replace(policy, published_at=timestamp),)
                response = client.get("/api/v1/meta")
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.headers["retry-after"], "60")
                self.validate(response.json(), "error")
                self.assertNotIn("PRIVATE", response.text)

    def test_metadata_remains_readable_at_daily_limit(self) -> None:
        usage = MemoryAnalysisUsage()
        for _ in range(100):
            usage.reserve()
        with TestClient(create_app(analysis_usage=usage)) as client:
            self.assertEqual(client.get("/api/v1/meta").json()["daily_remaining"], 0)
            self.assertEqual(client.get("/health").status_code, 200)
        self.assertEqual(usage.remaining(), 0)

    def test_catalog_and_usage_failures_return_safe_errors_without_fictional_fallback(self) -> None:
        class BrokenPolicies:
            def list_published(self):
                raise PolicyCatalogUnavailable("PRIVATE database password")

        class BrokenUsage:
            def remaining(self):
                raise AnalysisUsageUnavailable("PRIVATE database password")

        for kwargs in ({"policy_repository": BrokenPolicies()}, {"analysis_usage": BrokenUsage()}):
            with self.subTest(kwargs=tuple(kwargs)), TestClient(create_app(**kwargs)) as client:
                response = client.get("/api/v1/meta")
                self.assertEqual(response.status_code, 503)
                self.validate(response.json(), "error")
                self.assertNotIn("PRIVATE", response.text)
                self.assertNotIn("data_updated_at", response.json())

    def test_zero_rate_is_verified_while_invalid_rates_fail_safely(self) -> None:
        app = create_app(policy_repository=MemoryPolicies(release_data()))
        with TestClient(app, raise_server_exceptions=False) as client:
            app.state.discount_rate = 0.0
            response = client.get("/api/v1/meta")
            self.assertEqual(response.json()["base_rate"], 0.0)
            self.assertEqual(response.json()["base_rate_status"], "verified")
            for value in (-1.0, float("nan"), float("inf")):
                app.state.discount_rate = value
                response = client.get("/api/v1/meta")
                self.assertEqual(response.status_code, 500)
                self.validate(response.json(), "error")

    def test_openapi_exposes_the_meta_response(self) -> None:
        app = create_app()
        schema = app.openapi()
        self.assertEqual(
            schema["paths"]["/api/v1/meta"]["get"]["responses"]["200"]["content"]
            ["application/json"]["schema"]["$ref"],
            "#/components/schemas/MetaResponse",
        )
