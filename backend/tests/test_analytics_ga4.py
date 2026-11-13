"""Exact outbound GA4 payload, sanitization, timeout, and concurrency tests."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import json
import time
import unittest
from typing import Any
from unittest.mock import ANY, AsyncMock, Mock, patch

from benefitfinder.application.analytics import AnalyticsEventRecord
from benefitfinder.infrastructure.ga4_analytics import (
    GA4MeasurementProtocolAnalytics,
    _MAX_STATUS_LINE_BYTES,
    _post_google_measurement_protocol,
    ga_client_id,
)

CLIENT_ID = "00000000-0000-4000-8000-000000000001"
LETTER_CLIENT_ID = "abcdef01-2345-4678-9abc-def012345678"
EXPECTED_GA_CLIENT_ID = "16384.9223372036854775809"
MEASUREMENT_ID = "G-0000000000"
API_SECRET = "fictional-test-only"
EXPECTED_URL = (
    "https://www.google-analytics.com/mp/collect"
    "?measurement_id=G-0000000000&api_secret=fictional-test-only"
)


class RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bytes, Mapping[str, str]]] = []

    async def __call__(
        self,
        url: str,
        body: bytes,
        headers: Mapping[str, str],
    ) -> int:
        self.calls.append((url, body, headers))
        return 204


class FakeReader:
    def __init__(
        self,
        *,
        status_line: bytes = b"",
        wait_forever: bool = False,
        error: Exception | None = None,
    ) -> None:
        self.status_line = status_line
        self.wait_forever = wait_forever
        self.error = error

    async def readline(self) -> bytes:
        if self.error is not None:
            raise self.error
        if self.wait_forever:
            await asyncio.Event().wait()
        return self.status_line


class FakeWriter:
    def __init__(self, *, shutdown_waits: bool = False) -> None:
        self.data = b""
        self.closed = False
        self.drained = False
        self.shutdown_waits = shutdown_waits
        self.transport = Mock()
        self.transport.abort.side_effect = self.close

    def write(self, data: bytes) -> None:
        self.data += data

    async def drain(self) -> None:
        self.drained = True

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        if self.shutdown_waits:
            await asyncio.Event().wait()


def record(name: str, **parameters: Any) -> AnalyticsEventRecord:
    return AnalyticsEventRecord(
        client_id=CLIENT_ID,
        session_id=1_794_580_000,
        name=name,
        parameters=parameters,
    )


EVENT_CASES: tuple[tuple[AnalyticsEventRecord, dict[str, Any]], ...] = (
    (record("consent_check"), {}),
    (record("remember_on"), {}),
    (record("step_view", step=1), {"step": 1}),
    (record("step_complete", step=13), {"step": 13}),
    (record("step_skip"), {}),
    (record("analyze_start"), {}),
    (
        record("analyze_success", duration_ms=120_000, portfolio_count=3),
        {"duration_ms": 120_000, "portfolio_count": 3},
    ),
    (record("analyze_fail", code="timeout"), {"code": "timeout"}),
    (record("period_change", period="all"), {"period": "all"}),
    (record("pv_toggle", on=False), {"on": False}),
    (record("combo_select", rank=3), {"rank": 3}),
    (record("detail_view", policy_id="youth:example:P1"), {"policy_id": "youth:example:P1"}),
    (
        record("apply_click", policy_id="youth:example:P1", **{"from": "detail"}, rank=None),
        {"policy_id": "youth:example:P1", "from": "detail"},
    ),
    (record("whatif_open"), {}),
    (record("whatif_select", scenario="age_plus_one"), {"scenario": "age_plus_one"}),
    (record("image_save"), {}),
    (record("clear_mine"), {}),
)


class GA4AnalyticsTests(unittest.IsolatedAsyncioTestCase):
    def adapter(self, transport: RecordingTransport) -> GA4MeasurementProtocolAnalytics:
        return GA4MeasurementProtocolAnalytics(
            MEASUREMENT_ID,
            API_SECRET,
            timeout_seconds=1,
            max_in_flight=4,
            transport=transport,
        )

    async def test_all_17_events_have_exact_minimal_mp_payloads(self) -> None:
        transport = RecordingTransport()
        adapter = self.adapter(transport)
        for event, expected_parameters in EVENT_CASES:
            with self.subTest(event=event.name):
                await adapter.send(event)

        self.assertEqual(len(transport.calls), 17)
        for (event, expected_parameters), (url, body, headers) in zip(
            EVENT_CASES,
            transport.calls,
            strict=True,
        ):
            with self.subTest(event=event.name):
                self.assertEqual(url, EXPECTED_URL)
                self.assertEqual(headers, {"Content-Type": "application/json"})
                payload = json.loads(body)
                expected_params = {
                    **expected_parameters,
                    "session_id": 1_794_580_000,
                    "engagement_time_msec": 1,
                }
                self.assertEqual(
                    payload,
                    {
                        "client_id": EXPECTED_GA_CLIENT_ID,
                        "events": [{"name": event.name, "params": expected_params}],
                        "consent": {
                            "ad_user_data": "DENIED",
                            "ad_personalization": "DENIED",
                        },
                    },
                )
                self.assertEqual(set(payload), {"client_id", "events", "consent"})
                self.assertEqual(set(payload["events"][0]), {"name", "params"})
                self.assertNotIn(CLIENT_ID, body.decode("utf-8"))
                for forbidden in (
                    "user_id",
                    "user_properties",
                    "client_ip",
                    "user_agent",
                    "page_location",
                    "profile",
                    "error_text",
                ):
                    self.assertNotIn(forbidden, payload["events"][0]["params"])

    async def test_apply_click_numeric_rank_is_forwarded(self) -> None:
        transport = RecordingTransport()
        await self.adapter(transport).send(
            record("apply_click", policy_id="youth:example:P3", **{"from": "result"}, rank=2)
        )
        payload = json.loads(transport.calls[0][1])
        self.assertEqual(payload["events"][0]["params"]["rank"], 2)

    async def test_every_contract_failure_code_is_forwarded(self) -> None:
        codes = (
            "http_error",
            "invalid_request",
            "unsupported_domain",
            "not_found",
            "method_not_allowed",
            "daily_limit_exceeded",
            "dependency_unavailable",
            "internal_error",
            "calculation_limit_exceeded",
            "timeout",
            "network_error",
            "invalid_response",
            "unknown",
        )
        transport = RecordingTransport()
        adapter = self.adapter(transport)
        for code in codes:
            await adapter.send(record("analyze_fail", code=code))
        self.assertEqual(len(transport.calls), len(codes))
        self.assertEqual(
            [json.loads(call[1])["events"][0]["params"]["code"] for call in transport.calls],
            list(codes),
        )

    def test_uuid_conversion_preserves_both_64_bit_halves(self) -> None:
        self.assertEqual(ga_client_id(CLIENT_ID), EXPECTED_GA_CLIENT_ID)
        self.assertEqual(
            ga_client_id("ffffffff-ffff-4fff-bfff-ffffffffffff"),
            "18446744073709506559.13835058055282163711",
        )
        for invalid in (
            LETTER_CLIENT_ID.upper(),
            "00000000-0000-1000-8000-000000000001",
            CLIENT_ID[:-1],
            None,
        ):
            with self.subTest(invalid=invalid):
                self.assertIsNone(ga_client_id(invalid))

    async def test_bypassed_or_malformed_records_are_dropped_before_transport(self) -> None:
        transport = RecordingTransport()
        adapter = self.adapter(transport)
        invalid_events = (
            record("consent_check", profile="private"),
            record("unknown_event"),
            AnalyticsEventRecord(CLIENT_ID, 1, "analyze_start", {"page_location": "https://private"}),
            AnalyticsEventRecord("not-a-uuid", 1, "analyze_start", {}),
            AnalyticsEventRecord(CLIENT_ID, True, "analyze_start", {}),
            record("step_view", step=True),
            record("step_view", step=14),
            record("detail_view", policy_id="youth:example:P1\nprivate"),
            AnalyticsEventRecord(CLIENT_ID, 1, [], {}),
        )
        for event in invalid_events:
            with self.subTest(event=event):
                await adapter.send(event)
        self.assertEqual(transport.calls, [])

    async def test_timeout_is_bounded_and_swallowed(self) -> None:
        async def slow_transport(url: str, body: bytes, headers: Mapping[str, str]) -> int:
            await asyncio.sleep(1)
            return 204

        adapter = GA4MeasurementProtocolAnalytics(
            MEASUREMENT_ID,
            API_SECRET,
            timeout_seconds=0.02,
            transport=slow_transport,
        )
        started = time.monotonic()
        await adapter.send(record("analyze_start"))
        self.assertLess(time.monotonic() - started, 0.2)

    async def test_raw_https_request_has_fixed_endpoint_and_only_protocol_headers(self) -> None:
        reader = FakeReader(status_line=b"HTTP/1.1 204 No Content\r\n")
        writer = FakeWriter()
        open_connection = AsyncMock(return_value=(reader, writer))
        body = b"{}"
        with patch(
            "benefitfinder.infrastructure.ga4_analytics.asyncio.open_connection",
            open_connection,
        ):
            status = await _post_google_measurement_protocol(
                EXPECTED_URL,
                body,
                {"Content-Type": "application/json"},
            )

        self.assertEqual(status, 204)
        self.assertTrue(writer.closed)
        self.assertTrue(writer.drained)
        expected_request = (
            b"POST /mp/collect?measurement_id=G-0000000000&api_secret=fictional-test-only HTTP/1.1\r\n"
            b"Host: www.google-analytics.com\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: 2\r\n"
            b"Connection: close\r\n\r\n"
            b"{}"
        )
        self.assertEqual(writer.data, expected_request)
        for forbidden in (b"Cookie:", b"User-Agent:", b"Referer:", b"X-Forwarded-For:"):
            self.assertNotIn(forbidden, writer.data)
        open_connection.assert_awaited_once_with(
            "www.google-analytics.com",
            443,
            ssl=ANY,
            server_hostname="www.google-analytics.com",
            limit=_MAX_STATUS_LINE_BYTES,
        )

    async def test_timeout_and_oversized_status_line_close_writer_and_stay_swallowed(self) -> None:
        cases = (
            FakeReader(wait_forever=True),
            FakeReader(error=ValueError("status_line_limit_exceeded")),
        )
        for reader in cases:
            with self.subTest(reader=reader):
                writer = FakeWriter()
                open_connection = AsyncMock(return_value=(reader, writer))
                adapter = GA4MeasurementProtocolAnalytics(
                    MEASUREMENT_ID,
                    API_SECRET,
                    timeout_seconds=0.02,
                    transport=_post_google_measurement_protocol,
                )
                with patch(
                    "benefitfinder.infrastructure.ga4_analytics.asyncio.open_connection",
                    open_connection,
                ):
                    await adapter.send(record("analyze_start"))
                self.assertTrue(writer.closed)

    async def test_concurrency_limit_drops_excess_calls_without_queueing(self) -> None:
        release = asyncio.Event()

        class BlockingTransport:
            def __init__(self) -> None:
                self.calls = 0
                self.active = 0
                self.maximum = 0

            async def __call__(self, url: str, body: bytes, headers: Mapping[str, str]) -> int:
                self.calls += 1
                self.active += 1
                self.maximum = max(self.maximum, self.active)
                try:
                    await release.wait()
                    return 204
                finally:
                    self.active -= 1

        transport = BlockingTransport()
        adapter = GA4MeasurementProtocolAnalytics(
            MEASUREMENT_ID,
            API_SECRET,
            timeout_seconds=1,
            max_in_flight=2,
            transport=transport,
        )
        tasks = [asyncio.create_task(adapter.send(record("analyze_start"))) for _ in range(6)]
        await asyncio.sleep(0.02)
        self.assertEqual(transport.calls, 2)
        self.assertEqual(transport.maximum, 2)
        release.set()
        await asyncio.gather(*tasks)
        self.assertEqual(transport.calls, 2)

    async def test_tls_shutdown_cannot_extend_the_request_deadline(self) -> None:
        readers = (
            FakeReader(wait_forever=True),
            FakeReader(status_line=b"HTTP/1.1 204 No Content\r\n"),
        )
        for reader in readers:
            with self.subTest(reader=reader):
                writer = FakeWriter(shutdown_waits=True)
                adapter = GA4MeasurementProtocolAnalytics(
                    MEASUREMENT_ID,
                    API_SECRET,
                    timeout_seconds=0.02,
                    transport=_post_google_measurement_protocol,
                )
                with patch(
                    "benefitfinder.infrastructure.ga4_analytics.asyncio.open_connection",
                    AsyncMock(return_value=(reader, writer)),
                ):
                    await asyncio.wait_for(adapter.send(record("analyze_start")), timeout=0.2)
                self.assertTrue(writer.closed)
                writer.transport.abort.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
