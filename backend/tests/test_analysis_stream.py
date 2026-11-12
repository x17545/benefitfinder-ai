"""Real ASGI streaming, deadline and cancellation checks with fictional profiles."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import threading
import time
from unittest import IsolatedAsyncioTestCase, TestCase

from fastapi import HTTPException
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker, validate
from referencing import Registry, Resource

from benefitfinder.api.analysis_response import wants_event_stream
from benefitfinder.bootstrap import create_app

ROOT = Path(__file__).resolve().parents[2]
PAYLOAD = json.loads((ROOT / "contracts/fixtures/recommendation-request.json").read_text())
SIMULATION = json.loads((ROOT / "contracts/fixtures/simulation-request.json").read_text())
SCHEMAS = [json.loads(path.read_text()) for path in (ROOT / "contracts/v1").glob("*.schema.json")]
REGISTRY = Registry().with_resources(
    (schema["$id"], Resource.from_contents(schema)) for schema in SCHEMAS
)


def contract(name):
    return json.loads((ROOT / "contracts/v1" / name).read_text())


def events(body):
    parsed = []
    for block in body.split("\n\n"):
        if block.startswith("event: "):
            lines = block.splitlines()
            parsed.append((lines[0][7:], json.loads(lines[1][6:])))
    return parsed


class AnalysisStreamTests(TestCase):
    def test_json_and_sse_have_the_same_result_and_progress_matches_contract(self):
        with TestClient(create_app()) as client:
            expected = client.post("/api/v1/recommendations", json=PAYLOAD).json()
            response = client.post(
                "/api/v1/recommendations", json=PAYLOAD, headers={"Accept": "text/event-stream"}
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response.headers["content-type"])
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["x-accel-buffering"], "no")
        received = events(response.text)
        self.assertEqual([kind for kind, _ in received], ["progress"] * 6 + ["result"])
        actual = received[-1][1]
        self.assertNotEqual(actual["request_id"], expected["request_id"])
        self.assertEqual(
            {key: value for key, value in actual.items() if key != "request_id"},
            {key: value for key, value in expected.items() if key != "request_id"},
        )
        for _, progress in received[:-1]:
            validate(progress, contract("sse-progress.schema.json"))
            self.assertEqual(set(progress), {"step", "state", "label"})
        self.assertEqual([value["state"] for _, value in received[:-1]], ["running", "done"] * 3)
        Draft202012Validator(
            contract("recommendation-response.schema.json"),
            registry=REGISTRY,
            format_checker=FormatChecker(),
        ).validate(received[-1][1])

    def test_pre_stream_validation_and_domain_errors_remain_json(self):
        with TestClient(create_app()) as client:
            for payload, status, code in (
                ({}, 422, "invalid_request"),
                ({**PAYLOAD, "domain": "unsupported"}, 400, "unsupported_domain"),
            ):
                with self.subTest(status=status):
                    response = client.post(
                        "/api/v1/recommendations",
                        json=payload,
                        headers={"Accept": "text/event-stream"},
                    )
                    self.assertEqual(response.status_code, status)
                    self.assertEqual(response.headers["content-type"], "application/json")
                    self.assertEqual(response.json()["code"], code)

    def test_stream_failure_ends_once_and_does_not_expose_exception(self):
        app = create_app()

        def fail(*args, **kwargs):
            raise RuntimeError("PRIVATE-profile-key-prompt")

        app.state.recommendation_service = fail
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/recommendations", json=PAYLOAD, headers={"Accept": "text/event-stream"}
            )
        received = events(response.text)
        self.assertEqual([kind for kind, _ in received], ["progress", "progress", "error"])
        self.assertEqual(received[1][1]["state"], "failed")
        self.assertEqual(received[-1][1]["code"], "internal_error")
        validate(received[-1][1], contract("error.schema.json"))
        self.assertNotIn("PRIVATE", response.text)

    def test_json_and_sse_reuse_registered_dependency_error_handler(self):
        app = create_app()

        def fail(*args, **kwargs):
            raise HTTPException(503, detail="PRIVATE-database-error")

        app.state.recommendation_service = fail
        with TestClient(app) as client:
            plain = client.post("/api/v1/recommendations", json=PAYLOAD)
            stream = client.post(
                "/api/v1/recommendations", json=PAYLOAD, headers={"Accept": "text/event-stream"}
            )
        self.assertEqual(plain.status_code, 503)
        self.assertEqual(plain.json()["code"], "dependency_unavailable")
        self.assertEqual(events(stream.text)[-1][1]["code"], "dependency_unavailable")
        self.assertNotIn("PRIVATE", plain.text + stream.text)

    def test_json_recommendation_and_simulation_deadlines(self):
        for path, payload, attribute in (
            ("recommendations", PAYLOAD, "recommendation_service"),
            ("simulations", SIMULATION, "simulation_service"),
        ):
            with self.subTest(path=path):
                app = create_app(analysis_timeout_seconds=0.01)
                original = getattr(app.state, attribute)

                def slow(*args, **kwargs):
                    time.sleep(0.06)
                    return original(*args, **kwargs)

                setattr(app.state, attribute, slow)
                with TestClient(app) as client:
                    response = client.post(f"/api/v1/{path}", json=payload)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()["code"], "calculation_limit_exceeded")

    def test_dependency_timeout_is_not_misreported_as_analysis_deadline(self):
        app = create_app()

        def fail(*args, **kwargs):
            raise TimeoutError("PRIVATE dependency timeout")

        app.state.recommendation_service = fail
        with TestClient(app) as client:
            response = client.post("/api/v1/recommendations", json=PAYLOAD)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["code"], "internal_error")

    def test_deadline_budget_is_positive_and_cannot_exceed_sixty_seconds(self):
        for invalid in (True, 0, -1, 60.1, float("nan"), float("inf"), "60"):
            with (
                self.subTest(value=invalid),
                self.assertRaisesRegex(ValueError, "invalid_analysis_timeout"),
            ):
                create_app(analysis_timeout_seconds=invalid)

    def test_accept_negotiation_respects_zero_quality(self):
        for accept in (
            "*/*",
            "application/json",
            "text/event-stream;q=0",
            "text/event-stream;q=bad",
        ):
            self.assertFalse(wants_event_stream(accept))
        self.assertTrue(wants_event_stream("text/event-stream, application/json"))
        self.assertTrue(wants_event_stream("Text/Event-Stream; q=0.5"))


class LiveStreamTests(IsolatedAsyncioTestCase):
    async def start_request(self, app):
        incoming = asyncio.Queue()
        incoming.put_nowait(
            {"type": "http.request", "body": json.dumps(PAYLOAD).encode(), "more_body": False}
        )
        sent = []
        progress_seen = asyncio.Event()
        terminal_seen = asyncio.Event()

        async def send(message):
            sent.append(message)
            if message["type"] == "http.response.body":
                body = message.get("body", b"")
                if b"event: progress" in body:
                    progress_seen.set()
                if b"event: error" in body or b"event: result" in body:
                    terminal_seen.set()

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "method": "POST",
            "scheme": "http",
            "path": "/api/v1/recommendations",
            "raw_path": b"/api/v1/recommendations",
            "query_string": b"",
            "root_path": "",
            "headers": [(b"content-type", b"application/json"), (b"accept", b"text/event-stream")],
            "client": ("127.0.0.1", 1234),
            "server": ("localhost", 80),
            "http_version": "1.1",
        }
        task = asyncio.create_task(app(scope, incoming.get, send))
        return task, incoming, sent, progress_seen, terminal_seen

    async def test_first_progress_is_sent_while_calculation_is_still_blocked(self):
        app = create_app()
        gate = threading.Event()
        original = app.state.recommendation_service

        def blocked(*args, **kwargs):
            assert gate.wait(2)
            return original(*args, **kwargs)

        app.state.recommendation_service = blocked
        task, _, sent, progress_seen, terminal_seen = await self.start_request(app)
        try:
            await asyncio.wait_for(progress_seen.wait(), 1)
            self.assertFalse(terminal_seen.is_set())
        finally:
            gate.set()
            await asyncio.wait_for(task, 2)
        body = b"".join(message.get("body", b"") for message in sent).decode()
        self.assertEqual(events(body)[-1][0], "result")

    async def test_stream_deadline_emits_error_before_blocked_worker_returns(self):
        app = create_app(analysis_timeout_seconds=0.02)
        gate = threading.Event()

        def blocked(*args, **kwargs):
            assert gate.wait(2)
            return {}

        app.state.recommendation_service = blocked
        task, _, sent, _, terminal_seen = await self.start_request(app)
        try:
            await asyncio.wait_for(terminal_seen.wait(), 1)
            await asyncio.wait_for(task, 1)
            body = b"".join(message.get("body", b"") for message in sent).decode()
            received = events(body)
            self.assertEqual([kind for kind, _ in received], ["progress", "progress", "error"])
            self.assertEqual(received[-1][1]["code"], "calculation_limit_exceeded")
            self.assertFalse(gate.is_set())
        finally:
            gate.set()
            await task

    async def test_disconnect_cancels_pending_explanation_and_sends_no_terminal_result(self):
        app = create_app()
        entered, cancelled = asyncio.Event(), asyncio.Event()

        class BlockedExplainer:
            async def enrich(self, response):
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

        app.state.recommendation_explainer = BlockedExplainer()
        task, incoming, sent, _, _ = await self.start_request(app)
        await asyncio.wait_for(entered.wait(), 1)
        incoming.put_nowait({"type": "http.disconnect"})
        await asyncio.wait_for(task, 1)
        self.assertTrue(cancelled.is_set())
        body = b"".join(message.get("body", b"") for message in sent).decode()
        self.assertTrue(all(kind == "progress" for kind, _ in events(body)))
