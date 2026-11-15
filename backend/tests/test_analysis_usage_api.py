"""Shared recommendation/simulation admission with fictional HTTP inputs."""

import copy
import json
from pathlib import Path
from unittest import TestCase

from fastapi.testclient import TestClient
from jsonschema import validate

from benefitfinder.application.analysis_usage import AnalysisUsageUnavailable
from benefitfinder.bootstrap import create_app
from benefitfinder.infrastructure.memory_analysis_usage import MemoryAnalysisUsage

ROOT = Path(__file__).resolve().parents[2]
RECOMMENDATION = json.loads((ROOT / "contracts/fixtures/recommendation-request.json").read_text())
SIMULATION = json.loads((ROOT / "contracts/fixtures/simulation-request.json").read_text())
ERROR_SCHEMA = json.loads((ROOT / "contracts/v1/error.schema.json").read_text())


class AnalysisUsageApiTests(TestCase):
    def test_recommendations_and_simulations_share_one_hundred_attempts(self):
        usage = MemoryAnalysisUsage()
        for _ in range(98):
            usage.reserve()
        app = create_app(analysis_usage=usage)
        with TestClient(app) as client:
            self.assertEqual(
                client.post("/api/v1/recommendations", json=RECOMMENDATION).status_code, 200
            )
            self.assertEqual(usage.remaining(), 1)
            self.assertEqual(client.post("/api/v1/simulations", json=SIMULATION).status_code, 200)
            self.assertEqual(usage.remaining(), 0)
            for path, payload in (("recommendations", RECOMMENDATION), ("simulations", SIMULATION)):
                response = client.post(
                    f"/api/v1/{path}", json=payload, headers={"Accept": "text/event-stream"}
                )
                self.assertEqual(response.status_code, 429)
                self.assertEqual(response.headers["content-type"], "application/json")
                self.assertEqual(response.json()["code"], "daily_limit_exceeded")
                validate(response.json(), ERROR_SCHEMA)
            self.assertEqual(client.get("/health").status_code, 200)
        self.assertEqual(usage.remaining(), 0)

    def test_schema_domain_and_scenario_validation_do_not_consume(self):
        usage = MemoryAnalysisUsage()
        app = create_app(analysis_usage=usage)
        bad_scenario = copy.deepcopy(SIMULATION)
        bad_scenario["scenario"] = "independent"
        bad_scenario["income_change"] = {"from_won": 100, "to_won": 200}
        with TestClient(app) as client:
            for path, payload, status in (
                ("recommendations", {}, 422),
                ("recommendations", {**RECOMMENDATION, "domain": "other"}, 400),
                ("simulations", {}, 422),
                ("simulations", {**SIMULATION, "domain": "other"}, 400),
                ("simulations", bad_scenario, 422),
            ):
                with self.subTest(path=path, status=status):
                    response = client.post(f"/api/v1/{path}", json=payload)
                    self.assertEqual(response.status_code, status)
        self.assertEqual(usage.remaining(), 100)

    def test_started_failure_consumes_once_and_retry_consumes_another_attempt(self):
        usage = MemoryAnalysisUsage()
        app = create_app(analysis_usage=usage)

        def fail(*args, **kwargs):
            raise RuntimeError("PRIVATE profile")

        app.state.recommendation_service = fail
        with TestClient(app, raise_server_exceptions=False) as client:
            for expected in (99, 98):
                response = client.post("/api/v1/recommendations", json=RECOMMENDATION)
                self.assertEqual(response.status_code, 500)
                self.assertNotIn("PRIVATE", response.text)
                self.assertEqual(usage.remaining(), expected)

    def test_usage_failure_fails_closed_without_exposing_connection_or_running_calculation(self):
        class BrokenUsage:
            def reserve(self):
                raise AnalysisUsageUnavailable("PRIVATE connection password")

        app = create_app(analysis_usage=BrokenUsage())
        calls = []

        def calculation(*args, **kwargs):
            calls.append(True)
            raise AssertionError("must not run")

        app.state.recommendation_service = calculation
        app.state.simulation_service = calculation
        with TestClient(app) as client:
            for path, payload in (("recommendations", RECOMMENDATION), ("simulations", SIMULATION)):
                response = client.post(f"/api/v1/{path}", json=payload)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()["code"], "dependency_unavailable")
                self.assertNotIn("PRIVATE", response.text)
        self.assertEqual(calls, [])

    def test_injected_usage_and_database_configuration_are_not_ambiguous(self):
        with self.assertRaisesRegex(ValueError, "ambiguous_analysis_usage"):
            create_app(
                analysis_usage=MemoryAnalysisUsage(), analysis_usage_database_url="fictional"
            )
