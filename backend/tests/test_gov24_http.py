"""Focused GOV24 HTTP adapter tests use an injected opener and no network."""

from __future__ import annotations

import io
import unittest
import urllib.error
import urllib.parse
import urllib.request
from email.message import Message

from benefitfinder.application.collection import SourceRequest
from benefitfinder.infrastructure.gov24_http import (
    Gov24HttpError,
    Gov24HttpFetcher,
    _NoRedirect,
)
from benefitfinder.ingestion.gov24 import gov24_request

KEY = "virtual-gov24-key"


class FakeResponse:
    def __init__(self, status: int, body: bytes, content_type: str = "application/json") -> None:
        self.status = status
        self.body = body
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def getcode(self) -> int:
        return self.status

    def read(self, limit: int = -1) -> bytes:
        return self.body if limit < 0 else self.body[:limit]


class FakeOpener:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[urllib.request.Request] = []

    def open(
        self,
        request: urllib.request.Request,
        timeout: float,
    ) -> FakeResponse:
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, FakeResponse)
        return outcome


def fetcher(
    opener: FakeOpener,
    *,
    retries: int = 3,
    waits: list[float] | None = None,
    min_interval: float = 0,
    max_response_bytes: int = 16 * 1024 * 1024,
    monotonic: object | None = None,
) -> Gov24HttpFetcher:
    kwargs: dict[str, object] = {
        "min_interval_seconds": min_interval,
        "max_retries": retries,
        "retry_backoff_seconds": 1.5,
        "sleep": (waits if waits is not None else []).append,
        "opener": opener,
        "max_response_bytes": max_response_bytes,
    }
    if monotonic is not None:
        kwargs["monotonic"] = monotonic
    return Gov24HttpFetcher(KEY, **kwargs)


class Gov24HttpTests(unittest.TestCase):
    def test_adds_documented_authorization_header_only_at_send_time(self) -> None:
        request = gov24_request("serviceList", "ID-1")
        opener = FakeOpener([FakeResponse(200, b'{"ok":true}')])
        response = fetcher(opener).fetch(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body, b'{"ok":true}')
        sent = opener.requests[0]
        self.assertEqual(sent.get_header("Authorization"), f"Infuser {KEY}")
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(sent.full_url).query)
        self.assertNotIn("serviceKey", query)
        self.assertNotIn("apiKeyNm", query)
        self.assertEqual(query["cond[서비스ID::EQ]"], ["ID-1"])
        self.assertEqual(query["perPage"], ["1"])
        self.assertEqual(query["returnType"], ["JSON"])
        self.assertNotIn(KEY, repr(fetcher(opener)))

    def test_rejects_other_hosts_paths_and_unbounded_query_shape_before_open(self) -> None:
        opener = FakeOpener([])
        adapter = fetcher(opener)
        valid = gov24_request("serviceList", "ID-1")
        for request in (
            SourceRequest("https://evil.invalid/gov24/v3/serviceList", valid.params),
            SourceRequest("http://api.odcloud.kr/api/gov24/v3/serviceList", valid.params),
            SourceRequest(
                "https://api.odcloud.kr/api/gov24/v3/serviceList/extra",
                valid.params,
            ),
            SourceRequest(valid.url, {**valid.params, "serviceKey": KEY}),
            SourceRequest(valid.url, {**valid.params, "perPage": "10"}),
        ):
            with self.subTest(url=request.url, params=request.params):
                with self.assertRaises(ValueError) as raised:
                    adapter.fetch(request)
                self.assertNotIn(KEY, str(raised.exception))
        self.assertEqual(opener.requests, [])

    def test_redirect_handler_never_follows_to_another_host(self) -> None:
        request = urllib.request.Request(
            "https://api.odcloud.kr/api/gov24/v3/serviceList"
        )
        redirected = _NoRedirect().redirect_request(
            request,
            io.BytesIO(b"redirect"),
            302,
            "Found",
            Message(),
            "https://evil.invalid/steal",
        )
        self.assertIsNone(redirected)

    def test_retries_429_and_5xx_then_returns_success(self) -> None:
        opener = FakeOpener(
            [
                FakeResponse(429, b"slow"),
                FakeResponse(503, b"busy"),
                FakeResponse(200, b"ok"),
            ]
        )
        waits: list[float] = []
        response = fetcher(opener, waits=waits).fetch(
            gov24_request("supportConditions", "ID-1")
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(opener.requests), 3)
        self.assertEqual(waits, [1.5, 3.0])

    def test_non_retryable_http_error_is_returned_as_response(self) -> None:
        headers = Message()
        headers["Content-Type"] = "text/plain"
        error = urllib.error.HTTPError(
            "https://api.odcloud.kr/api/gov24/v3/serviceList",
            404,
            "Not Found",
            headers,
            io.BytesIO(b"missing"),
        )
        opener = FakeOpener([error])
        response = fetcher(opener).fetch(gov24_request("serviceList", "ID-1"))
        self.assertEqual((response.status_code, response.body), (404, b"missing"))
        self.assertEqual(len(opener.requests), 1)

    def test_transport_failures_are_bounded_and_never_echo_exception_text(self) -> None:
        opener = FakeOpener([
            urllib.error.URLError(f"secret {KEY}"),
            urllib.error.URLError(f"secret {KEY}"),
        ])
        waits: list[float] = []
        with self.assertRaises(Gov24HttpError) as raised:
            fetcher(opener, retries=1, waits=waits).fetch(
                gov24_request("serviceDetail", "ID-1")
            )
        self.assertEqual(raised.exception.code, "transport_failed")
        self.assertEqual(str(raised.exception), "transport_failed")
        self.assertNotIn(KEY, repr(raised.exception))
        self.assertEqual(waits, [1.5])

    def test_response_limit_is_enforced(self) -> None:
        opener = FakeOpener([FakeResponse(200, b"12345")])
        with self.assertRaises(Gov24HttpError) as raised:
            fetcher(opener, max_response_bytes=4).fetch(
                gov24_request("serviceList", "ID-1")
            )
        self.assertEqual(raised.exception.code, "response_too_large")

    def test_paces_requests_and_validates_constructor(self) -> None:
        clock = iter([10.0, 10.1, 10.2])
        opener = FakeOpener([FakeResponse(200, b"a"), FakeResponse(200, b"b")])
        waits: list[float] = []
        adapter = fetcher(
            opener,
            waits=waits,
            min_interval=1.0,
            monotonic=lambda: next(clock),
        )
        adapter.fetch(gov24_request("serviceList", "ID-1"))
        adapter.fetch(gov24_request("serviceList", "ID-2"))
        self.assertEqual(len(waits), 1)
        self.assertAlmostEqual(waits[0], 0.9)
        with self.assertRaises(ValueError):
            Gov24HttpFetcher("  ")
        with self.assertRaises(ValueError):
            Gov24HttpFetcher(KEY, max_response_bytes=16 * 1024 * 1024 + 1)
        with self.assertRaises(ValueError):
            Gov24HttpFetcher(KEY + "\r\nInjected: value")


if __name__ == "__main__":
    unittest.main()
