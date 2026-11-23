"""HTTP admission tests for shared analysis concurrency."""

import copy
import json
from pathlib import Path
from unittest import TestCase

from fastapi.testclient import TestClient

from benefitfinder.bootstrap import create_app
from benefitfinder.infrastructure.memory_analysis_usage import MemoryAnalysisUsage

ROOT = Path(__file__).resolve().parents[2]
RECOMMENDATION = json.loads(
    (ROOT / "contracts/fixtures/recommendation-request.json").read_text(encoding="utf-8")
)
SIMULATION = json.loads(
    (ROOT / "contracts/fixtures/simulation-request.json").read_text(encoding="utf-8")
)


class AnalysisConcurrencyApiTests(TestCase):
    def test_busy_recommendation_does_not_consume_simulation_attempt(self):
        usage = MemoryAnalysisUsage()
        app = create_app(analysis_usage=usage)

        guard = getattr(app.state, "analysis_concurrency", None)
        self.assertIsNotNone(
            guard,
            "create_app must register shared analysis_concurrency",
        )

        self.assertTrue(guard.try_acquire())
        try:
            with TestClient(app) as client:
                response = client.post(
                    "/api/v1/simulations",
                    json=copy.deepcopy(SIMULATION),
                )

            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["code"], "analysis_busy")
            self.assertEqual(usage.remaining(), 100)
        finally:
            guard.release()
    def test_app_shutdown_closes_analysis_executor(self):
        app = create_app()
        guard = app.state.analysis_concurrency

        with TestClient(app) as client:
            self.assertEqual(client.get("/health").status_code, 200)
            self.assertFalse(guard._executor._shutdown)

        self.assertTrue(
            guard._executor._shutdown,
            "FastAPI shutdown did not close analysis executor",
        )

    def test_sequential_simulations_release_request_slot(self):
        usage = MemoryAnalysisUsage()
        app = create_app(analysis_usage=usage)
        guard = app.state.analysis_concurrency

        with TestClient(app) as client:
            for attempt in range(2):
                response = client.post(
                    "/api/v1/simulations",
                    json=copy.deepcopy(SIMULATION),
                )

                self.assertEqual(
                    response.status_code,
                    200,
                    f"simulation request {attempt + 1}: {response.text}",
                )

                self.assertTrue(
                    guard.try_acquire(),
                    f"request slot was not released after request {attempt + 1}",
                )
                guard.release()

                self.assertEqual(
                    usage.remaining(),
                    99 - attempt,
                    "analysis usage should be consumed exactly once per request",
                )
    def test_unstarted_sse_iterator_releases_request_slot(self):
        import asyncio
        from types import SimpleNamespace

        from benefitfinder.api.analysis_response import analysis_response

        app = create_app()
        guard = app.state.analysis_concurrency
        lease = guard.acquire_request()

        request = SimpleNamespace(
            app=app,
            state=SimpleNamespace(analysis_lease=lease),
        )

        async def analyze(progress):
            return {"unexpected": True}

        async def scenario():
            response = await analysis_response(
                request,
                analyze,
                stream=True,
            )

            await response.body_iterator.aclose()

            self.assertTrue(
                guard.try_acquire(),
                "unstarted SSE iterator did not release request slot",
            )
            guard.release()

        try:
            asyncio.run(scenario())
        finally:
            lease.close()
            guard.shutdown()
