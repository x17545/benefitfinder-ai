"""Focused official-document HTTP tests use fictional URLs and fake openers."""

from __future__ import annotations

import io
import unittest
import urllib.error
import urllib.parse
import urllib.request
from email.message import Message

from benefitfinder.application.official_documents import OfficialDocumentTarget
from benefitfinder.infrastructure.official_document_http import (
    MAX_RESPONSE_BYTES,
    OfficialDocumentHttpError,
    OfficialDocumentHttpFetcher,
    _NoRedirect,
)

DOCUMENT_URL = "https://official.example.invalid/documents/housing.pdf"
POST_URL = "https://official.example.invalid/documents/download"
OTHER_URL = "https://official.example.invalid/documents/employment.html"


class FakeResponse:
    def __init__(self, status: int, body: bytes, content_type: str = "application/pdf") -> None:
        self.status = status
        self.body = body
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.read_limits: list[int] = []

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def getcode(self) -> int:
        return self.status

    def read(self, limit: int = -1) -> bytes:
        self.read_limits.append(limit)
        return self.body if limit < 0 else self.body[:limit]


class FakeOpener:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[urllib.request.Request] = []
        self.timeouts: list[float] = []

    def open(
        self,
        request: urllib.request.Request,
        timeout: float,
    ) -> FakeResponse:
        self.requests.append(request)
        self.timeouts.append(timeout)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, FakeResponse)
        return outcome


def target(
    url: str = DOCUMENT_URL,
    *,
    format: str = "pdf",
    method: str = "GET",
    form: tuple[tuple[str, str], ...] = (),
) -> OfficialDocumentTarget:
    return OfficialDocumentTarget(
        external_id="notice-1",
        policy_id="youth:housing-1",
        url=url,
        format=format,
        method=method,
        form=form,
    )


def fetcher(
    opener: FakeOpener,
    *,
    allowed_urls: tuple[str, ...] = (DOCUMENT_URL,),
    retries: int = 2,
    waits: list[float] | None = None,
    min_interval: float = 0,
    backoff: float = 0.5,
    max_response_bytes: int = MAX_RESPONSE_BYTES,
    timeout: float = 30,
    monotonic: object | None = None,
) -> OfficialDocumentHttpFetcher:
    kwargs: dict[str, object] = {
        "timeout_seconds": timeout,
        "min_interval_seconds": min_interval,
        "max_retries": retries,
        "retry_backoff_seconds": backoff,
        "max_response_bytes": max_response_bytes,
        "sleep": (waits if waits is not None else []).append,
        "opener": opener,
    }
    if monotonic is not None:
        kwargs["monotonic"] = monotonic
    return OfficialDocumentHttpFetcher(allowed_urls, **kwargs)


class OfficialDocumentHttpTests(unittest.TestCase):
    def test_get_uses_the_exact_allowed_public_url_without_credentials(self) -> None:
        opener = FakeOpener([FakeResponse(200, b"%PDF-virtual")])
        adapter = fetcher(opener, allowed_urls=(DOCUMENT_URL, DOCUMENT_URL))
        response = adapter.fetch(target())
        self.assertEqual((response.status_code, response.body), (200, b"%PDF-virtual"))
        self.assertEqual(response.content_type, "application/pdf")
        sent = opener.requests[0]
        self.assertEqual(sent.full_url, DOCUMENT_URL)
        self.assertEqual(sent.get_method(), "GET")
        self.assertIsNone(sent.data)
        self.assertIsNone(sent.get_header("Authorization"))
        self.assertEqual(opener.timeouts, [30.0])
        self.assertNotIn(DOCUMENT_URL, repr(adapter))

    def test_post_encodes_public_form_fields_for_namgu_pdf_download(self) -> None:
        opener = FakeOpener([FakeResponse(200, b"%PDF-virtual")])
        response = fetcher(opener, allowed_urls=(POST_URL,)).fetch(
            target(
                POST_URL,
                method="POST",
                form=(
                    ("fileMaskNm", "housing notice 2026.pdf"),
                    ("publicFlag", "Y"),
                ),
            )
        )
        self.assertEqual(response.status_code, 200)
        sent = opener.requests[0]
        self.assertEqual(sent.get_method(), "POST")
        self.assertEqual(
            sent.get_header("Content-type"),
            "application/x-www-form-urlencoded",
        )
        self.assertEqual(
            urllib.parse.parse_qs((sent.data or b"").decode("ascii")),
            {"fileMaskNm": ["housing notice 2026.pdf"], "publicFlag": ["Y"]},
        )

    def test_constructor_rejects_unsafe_urls_and_out_of_bound_settings(self) -> None:
        unsafe_lists: list[object] = [
            [],
            (),
            ("http://official.example.invalid/documents/housing.pdf",),
            ("https://user:pass@official.example.invalid/documents/housing.pdf",),
            ("https://official.example.invalid/documents/housing.pdf#section",),
            ("https://official.example.invalid/documents/housing.pdf?api_key=virtual",),
            ("https://official.example.invalid/documents/housing%0A.pdf",),
        ]
        for allowed_urls in unsafe_lists:
            with self.subTest(allowed_urls=allowed_urls):
                with self.assertRaises(ValueError) as raised:
                    OfficialDocumentHttpFetcher(allowed_urls)  # type: ignore[arg-type]
                self.assertEqual(
                    str(raised.exception),
                    "invalid_official_document_http_config",
                )
                self.assertNotIn("official.example.invalid", str(raised.exception))
        for kwargs in (
            {"timeout_seconds": 30.1},
            {"max_retries": 3},
            {"max_response_bytes": MAX_RESPONSE_BYTES + 1},
            {"retry_backoff_seconds": 2.1},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    OfficialDocumentHttpFetcher((DOCUMENT_URL,), **kwargs)

    def test_fetch_rejects_a_different_url_before_opening(self) -> None:
        opener = FakeOpener([])
        with self.assertRaises(OfficialDocumentHttpError) as raised:
            fetcher(opener).fetch(target(OTHER_URL, format="html"))
        self.assertEqual(raised.exception.code, "url_not_allowed")
        self.assertNotIn(OTHER_URL, str(raised.exception))
        self.assertNotIn(OTHER_URL, repr(raised.exception))
        self.assertEqual(opener.requests, [])

    def test_fetch_independently_rejects_invalid_method_and_form_shape(self) -> None:
        opener = FakeOpener([])
        for method, form in (("DELETE", ()), ("GET", (("fileMaskNm", "virtual.pdf"),))):
            invalid = OfficialDocumentTarget.__new__(OfficialDocumentTarget)
            object.__setattr__(invalid, "external_id", "notice-1")
            object.__setattr__(invalid, "policy_id", "youth:housing-1")
            object.__setattr__(invalid, "url", DOCUMENT_URL)
            object.__setattr__(invalid, "format", "pdf")
            object.__setattr__(invalid, "method", method)
            object.__setattr__(invalid, "form", form)
            object.__setattr__(invalid, "categories", ())
            with self.subTest(method=method, form=form):
                with self.assertRaises(OfficialDocumentHttpError) as raised:
                    fetcher(opener).fetch(invalid)
                self.assertEqual(raised.exception.code, "invalid_target")
        self.assertEqual(opener.requests, [])

    def test_redirect_handler_and_redirect_response_are_never_followed(self) -> None:
        request = urllib.request.Request(DOCUMENT_URL)
        redirected = _NoRedirect().redirect_request(
            request,
            io.BytesIO(b"redirect"),
            302,
            "Found",
            Message(),
            "https://evil.example.invalid/steal",
        )
        self.assertIsNone(redirected)

        headers = Message()
        headers["Content-Type"] = "text/html"
        headers["Location"] = "https://evil.example.invalid/steal"
        error = urllib.error.HTTPError(
            DOCUMENT_URL,
            302,
            "Found",
            headers,
            io.BytesIO(b"moved"),
        )
        opener = FakeOpener([error])
        response = fetcher(opener).fetch(target())
        self.assertEqual((response.status_code, response.body), (302, b"moved"))
        self.assertEqual(len(opener.requests), 1)

    def test_404_and_410_are_returned_without_retry(self) -> None:
        for status in (404, 410):
            headers = Message()
            headers["Content-Type"] = "text/plain"
            error = urllib.error.HTTPError(
                DOCUMENT_URL,
                status,
                "not found",
                headers,
                io.BytesIO(b"absent"),
            )
            waits: list[float] = []
            opener = FakeOpener([error])
            with self.subTest(status=status):
                response = fetcher(opener, waits=waits).fetch(target())
                self.assertEqual((response.status_code, response.body), (status, b"absent"))
                self.assertEqual(len(opener.requests), 1)
                self.assertEqual(waits, [])

    def test_retries_429_and_5xx_with_bounded_exponential_backoff(self) -> None:
        opener = FakeOpener(
            [
                FakeResponse(429, b"slow"),
                FakeResponse(503, b"busy"),
                FakeResponse(200, b"ok"),
            ]
        )
        waits: list[float] = []
        response = fetcher(opener, waits=waits, backoff=1.25).fetch(target())
        self.assertEqual(response.body, b"ok")
        self.assertEqual(len(opener.requests), 3)
        self.assertEqual(waits, [1.25, 2.0])

    def test_exhausted_5xx_returns_last_response_and_transport_is_safely_bounded(self) -> None:
        opener = FakeOpener(
            [FakeResponse(503, b"one"), FakeResponse(502, b"two"), FakeResponse(500, b"three")]
        )
        waits: list[float] = []
        response = fetcher(opener, waits=waits).fetch(target())
        self.assertEqual((response.status_code, response.body), (500, b"three"))
        self.assertEqual(len(opener.requests), 3)
        self.assertEqual(waits, [0.5, 1.0])

        transport = FakeOpener(
            [
                urllib.error.URLError(f"secret {DOCUMENT_URL}"),
                urllib.error.URLError(f"secret {DOCUMENT_URL}"),
                urllib.error.URLError(f"secret {DOCUMENT_URL}"),
            ]
        )
        transport_waits: list[float] = []
        with self.assertRaises(OfficialDocumentHttpError) as raised:
            fetcher(transport, waits=transport_waits).fetch(target())
        self.assertEqual(raised.exception.code, "transport_failed")
        self.assertEqual(str(raised.exception), "transport_failed")
        self.assertNotIn("secret", str(raised.exception))
        self.assertNotIn(DOCUMENT_URL, repr(raised.exception))
        self.assertEqual(len(transport.requests), 3)
        self.assertEqual(transport_waits, [0.5, 1.0])

    def test_body_limit_applies_to_success_and_http_error_bodies(self) -> None:
        oversized = FakeResponse(200, b"12345")
        with self.assertRaises(OfficialDocumentHttpError) as raised:
            fetcher(FakeOpener([oversized]), max_response_bytes=4).fetch(target())
        self.assertEqual(raised.exception.code, "response_too_large")
        self.assertEqual(oversized.read_limits, [5])

        error_body = io.BytesIO(b"12345")
        error = urllib.error.HTTPError(
            DOCUMENT_URL,
            404,
            "not found",
            Message(),
            error_body,
        )
        with self.assertRaises(OfficialDocumentHttpError) as raised:
            fetcher(FakeOpener([error]), max_response_bytes=4).fetch(target())
        self.assertEqual(raised.exception.code, "response_too_large")
        self.assertTrue(error_body.closed)

    def test_paces_requests_with_injected_clock_and_timeout(self) -> None:
        clock = iter([10.0, 10.1, 10.2])
        opener = FakeOpener([FakeResponse(200, b"a"), FakeResponse(200, b"b")])
        waits: list[float] = []
        adapter = fetcher(
            opener,
            waits=waits,
            min_interval=1.0,
            timeout=12.5,
            monotonic=lambda: next(clock),
        )
        adapter.fetch(target())
        adapter.fetch(target())
        self.assertEqual(len(waits), 1)
        self.assertAlmostEqual(waits[0], 0.9)
        self.assertEqual(opener.timeouts, [12.5, 12.5])


if __name__ == "__main__":
    unittest.main()
