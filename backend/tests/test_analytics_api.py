"""F11 analytics HTTP boundary and fail-closed configuration tests."""

from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import subprocess
import sys
import unittest
from typing import Any

from fastapi.testclient import TestClient

from benefitfinder.application.analytics import AnalyticsEventRecord, AnalyticsService
from benefitfinder.bootstrap import analytics_service_from_environment, create_app
from benefitfinder.infrastructure.ga4_analytics import GA4MeasurementProtocolAnalytics

CLIENT_ID = "00000000-0000-4000-8000-000000000001"
LETTER_CLIENT_ID = "abcdef01-2345-4678-9abc-def012345678"
VALID_ENV = {
    "GA4_ENABLED": "true",
    "GA4_MEASUREMENT_ID": "G-0000000000",
    "GA4_API_SECRET": "fictional-test-only",
}


class RecordingSender:
    def __init__(self) -> None:
        self.events: list[AnalyticsEventRecord] = []

    async def send(self, event: AnalyticsEventRecord) -> None:
        self.events.append(event)


class FailingSender:
    async def send(self, event: AnalyticsEventRecord) -> None:
        raise RuntimeError("private-egress-detail-must-not-leak")


def event(name: str, **parameters: Any) -> dict[str, Any]:
    return {"name": name, **parameters}


ALL_EVENTS: tuple[dict[str, Any], ...] = (
    event("consent_check"),
    event("remember_on"),
    event("step_view", step=1),
    event("step_complete", step=13),
    event("step_skip"),
    event("analyze_start"),
    event("analyze_success", duration_ms=120_000, portfolio_count=3),
    event("analyze_fail", code="invalid_response"),
    event("period_change", period="all"),
    event("pv_toggle", on=True),
    event("combo_select", rank=3),
    event("detail_view", policy_id="youth:example:P1"),
    event("apply_click", policy_id="youth:example:P1", **{"from": "result"}, rank=None),
    event("whatif_open"),
    event("whatif_select", scenario="age_plus_one"),
    event("image_save"),
    event("clear_mine"),
)


def request_body(item: dict[str, Any]) -> dict[str, Any]:
    return {"client_id": CLIENT_ID, "session_id": 1_794_580_000, "event": deepcopy(item)}


class AnalyticsApiTests(unittest.TestCase):
    def new_client(self, service: AnalyticsService) -> TestClient:
        client = TestClient(create_app(analytics_service=service))
        self.addCleanup(client.close)
        return client

    def test_config_is_exact_and_never_cached(self) -> None:
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                sender = RecordingSender()
                client = self.new_client(AnalyticsService(enabled=enabled, sender=sender))
                response = client.get("/api/v1/analytics/config")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"enabled": enabled})
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertEqual(sender.events, [])

    def test_openapi_keeps_config_exact_and_post_as_204(self) -> None:
        client = self.new_client(AnalyticsService())
        spec = client.get("/openapi.json").json()
        self.assertIn("/api/v1/analytics/config", spec["paths"])
        self.assertIn("204", spec["paths"]["/api/v1/analytics/events"]["post"]["responses"])
        config_ref = spec["paths"]["/api/v1/analytics/config"]["get"]["responses"]["200"][
            "content"
        ]["application/json"]["schema"]["$ref"]
        config_schema = spec["components"]["schemas"][config_ref.rsplit("/", 1)[-1]]
        self.assertEqual(config_schema["required"], ["enabled"])
        self.assertEqual(set(config_schema["properties"]), {"enabled"})
        self.assertIs(config_schema["additionalProperties"], False)
        self.assertEqual(config_schema["properties"]["enabled"]["type"], "boolean")

    def test_all_17_contract_events_reach_the_application_boundary(self) -> None:
        sender = RecordingSender()
        client = self.new_client(AnalyticsService(enabled=True, sender=sender))
        for item in ALL_EVENTS:
            with self.subTest(event=item["name"]):
                response = client.post("/api/v1/analytics/events", json=request_body(item))
                self.assertEqual(response.status_code, 204, response.text)
                self.assertEqual(response.content, b"")
                self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(len(sender.events), 17)
        self.assertEqual([record.name for record in sender.events], [item["name"] for item in ALL_EVENTS])
        self.assertNotIn("rank", sender.events[12].parameters)

    def test_disabled_analytics_never_contacts_sender(self) -> None:
        sender = RecordingSender()
        client = self.new_client(AnalyticsService(enabled=False, sender=sender))
        response = client.post("/api/v1/analytics/events", json=request_body(event("analyze_start")))
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(sender.events, [])

    def test_sender_failure_stays_best_effort_and_does_not_echo_detail(self) -> None:
        client = self.new_client(AnalyticsService(enabled=True, sender=FailingSender()))
        response = client.post("/api/v1/analytics/events", json=request_body(event("analyze_start")))
        self.assertEqual(response.status_code, 204)
        self.assertNotIn("private-egress-detail", response.text)

    def test_mp_egress_failure_does_not_turn_the_route_into_500(self) -> None:
        async def failing_transport(url: str, body: bytes, headers: Any) -> int:
            raise OSError("private-network-endpoint-and-secret")

        adapter = GA4MeasurementProtocolAnalytics(
            VALID_ENV["GA4_MEASUREMENT_ID"],
            VALID_ENV["GA4_API_SECRET"],
            transport=failing_transport,
        )
        client = self.new_client(AnalyticsService(enabled=True, sender=adapter))
        response = client.post("/api/v1/analytics/events", json=request_body(event("consent_check")))
        self.assertEqual(response.status_code, 204)
        self.assertNotIn("private-network", response.text)

    def test_strict_validation_returns_fixed_error_and_no_input_echo(self) -> None:
        sender = RecordingSender()
        client = self.new_client(AnalyticsService(enabled=True, sender=sender))
        baseline = request_body(event("step_view", step=1))
        secret = "profile-secret-990101-1234567"
        cases: list[dict[str, Any]] = []

        extra_top = deepcopy(baseline)
        extra_top["profile"] = secret
        cases.append(extra_top)
        extra_nested = deepcopy(baseline)
        extra_nested["event"]["age"] = 27
        cases.append(extra_nested)
        for key, value in (
            ("client_id", LETTER_CLIENT_ID.upper()),
            ("client_id", CLIENT_ID[:-1]),
            ("client_id", "00000000-0000-1000-8000-000000000001"),
            ("session_id", True),
            ("session_id", "1"),
            ("session_id", 0),
            ("session_id", 10_000_000_000),
        ):
            invalid = deepcopy(baseline)
            invalid[key] = value
            cases.append(invalid)
        for value in (True, 1.0, 0, 14, secret):
            invalid = deepcopy(baseline)
            invalid["event"]["step"] = value
            cases.append(invalid)
        cases.extend(
            (
                request_body(event("unknown_event")),
                request_body(event("analyze_success", duration_ms=0)),
                request_body(event("pv_toggle", on=1)),
                request_body(event("apply_click", policy_id="youth:example:P1", **{"from": "detail"})),
                request_body(event("apply_click", policy_id="youth:example:P1", **{"from": "detail"}, rank=0)),
                request_body(
                    event(
                        "detail_view",
                        policy_id="youth:example:P1\nprivate",
                    )
                ),
                request_body(
                    event(
                        "apply_click",
                        policy_id="youth:example:P1",
                        **{"from": "detail", "url": secret},
                        rank=None,
                    )
                ),
            )
        )
        for invalid in cases:
            with self.subTest(payload=invalid):
                response = client.post("/api/v1/analytics/events", json=invalid)
                self.assertEqual(response.status_code, 422)
                body = response.json()
                self.assertEqual(set(body), {"request_id", "code", "message"})
                self.assertEqual(body["code"], "invalid_request")
                self.assertNotIn(secret, response.text)
        self.assertEqual(sender.events, [])

    def test_environment_configuration_is_disabled_by_default_and_fail_closed(self) -> None:
        disabled_cases = (
            {},
            {"GA4_ENABLED": "false", "GA4_MEASUREMENT_ID": "broken", "GA4_API_SECRET": ""},
        )
        for environ in disabled_cases:
            with self.subTest(environ=environ):
                service = analytics_service_from_environment(environ)
                self.assertFalse(service.enabled)
                self.assertIsNone(service.sender)

        enabled = analytics_service_from_environment(VALID_ENV)
        self.assertTrue(enabled.enabled)
        self.assertIsInstance(enabled.sender, GA4MeasurementProtocolAnalytics)

        secret = "private-secret-must-not-appear"
        invalid_cases = (
            {"GA4_ENABLED": "yes"},
            {"GA4_ENABLED": "true"},
            {"GA4_ENABLED": "true", "GA4_MEASUREMENT_ID": "G-0000000000"},
            {
                "GA4_ENABLED": "true",
                "GA4_MEASUREMENT_ID": "g-0000000000",
                "GA4_API_SECRET": secret,
            },
            {
                "GA4_ENABLED": "true",
                "GA4_MEASUREMENT_ID": "G-0000000000",
                "GA4_API_SECRET": "",
            },
        )
        for environ in invalid_cases:
            with self.subTest(environ=environ):
                with self.assertRaises(ValueError) as raised:
                    analytics_service_from_environment(environ)
                self.assertEqual(str(raised.exception), "invalid_analytics_configuration")
                self.assertNotIn(secret, str(raised.exception))

    def test_main_startup_raises_only_fixed_config_error(self) -> None:
        secret = "private-startup-secret-must-not-appear"
        env = os.environ.copy()
        env.update(
            {
                "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
                "GA4_ENABLED": "true",
                "GA4_MEASUREMENT_ID": "not-a-ga4-id",
                "GA4_API_SECRET": secret,
            }
        )
        result = subprocess.run(
            [sys.executable, "-c", "import benefitfinder.main"],
            cwd=Path(__file__).resolve().parents[2],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid_analytics_configuration", result.stderr)
        self.assertNotIn(secret, result.stderr)


if __name__ == "__main__":
    unittest.main()
