"""Independent checks for bounded youthcenter response diagnostics."""

from __future__ import annotations

import json
import os
from collections import deque
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from benefitfinder.application.collection import SourceResponse, UnsafeSourceResponse
from benefitfinder.ingestion.collect import (
    CollectionError,
    CollectionResult,
    collect_youthcenter,
)
from benefitfinder.ingestion.youthcenter import YOUTHCENTER_POLICY_LIST
from benefitfinder.jobs.__main__ import main
from benefitfinder.jobs.youthcenter_refresh import (
    _failure_diagnostic,
    run_youthcenter_refresh,
)

AT = datetime(2026, 11, 17, 3, 0, tzinfo=timezone.utc)
CONFIG = replace(YOUTHCENTER_POLICY_LIST, page_size=1)
KEY = "virtual-diagnostic-key"
PRIVATE_MESSAGE = "virtual-private-error-message"
PRIVATE_URL = "https://private.invalid/path?token=virtual-token"
PRIVATE_TITLE = "virtual-private-title"
PRIVATE_CODE = "virtual-private-code"
PRIVATE_HEADER = "virtual-private-header"
DIAGNOSTIC_PREFIX = "온통청년 수집 오류: "
DIAGNOSTIC_KEYS = {
    "attempt",
    "page",
    "error_code",
    "http_status",
    "api_code",
    "response_format",
    "content_type",
    "response_bytes",
    "response_sha256",
    "error_identifier",
    "error_identifier_source",
}
RESPONSE_METADATA = (
    "response_format",
    "content_type",
    "response_bytes",
    "response_sha256",
    "error_identifier",
    "error_identifier_source",
)


class _Fetcher:
    def __init__(self, *outcomes: SourceResponse | BaseException) -> None:
        self.outcomes = deque(outcomes)
        self.requests = []

    def fetch(self, request):
        self.requests.append(request)
        outcome = self.outcomes.popleft()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class _Snapshots:
    def save(self, snapshot) -> str:
        return "virtual-snapshot"


class _Versions:
    def latest_hashes(self, source_name: str):
        return {}

    def save_versions(self, records, changes, *, checked_at) -> None:
        return None


def _json_body(**values: object) -> bytes:
    document = {"errorMsg": f"invalid api key. {PRIVATE_MESSAGE} {PRIVATE_URL}"}
    document.update(values)
    return json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _collect(*outcomes: SourceResponse | BaseException):
    fetcher = _Fetcher(*outcomes)
    result = collect_youthcenter(
        CONFIG,
        fetcher,
        _Snapshots(),
        _Versions(),
        lambda: AT,
    )
    return result, fetcher


def _diagnostics(stderr: str) -> list[dict[str, object]]:
    return [
        json.loads(line.removeprefix(DIAGNOSTIC_PREFIX))
        for line in stderr.splitlines()
        if line.startswith(DIAGNOSTIC_PREFIX)
    ]


class CollectionResponseDiagnosticTests(TestCase):
    def test_json_error_code_has_bounded_metadata_without_message_or_url(self) -> None:
        body = _json_body(errorCode="e003")
        result, _ = _collect(
            SourceResponse(400, body, "Application/JSON; charset=utf-8")
        )

        self.assertFalse(result.complete)
        error = result.errors[0]
        self.assertEqual(
            (error.page, error.code, error.http_status, error.api_code),
            (1, "http_status", 400, "e003"),
        )
        self.assertEqual(error.response_format, "json")
        self.assertEqual(error.content_type, "application/json")
        self.assertEqual(error.response_bytes, len(body))
        self.assertEqual(error.response_sha256, sha256(body).hexdigest())
        self.assertEqual(error.error_identifier, "e003")
        self.assertEqual(error.error_identifier_source, "errorCode")
        serialized = repr(error)
        for private in (PRIVATE_MESSAGE, PRIVATE_URL, "charset=utf-8"):
            self.assertNotIn(private, serialized)

    def test_invalid_api_key_response_uses_error_code_e001(self) -> None:
        body = _json_body(errorCode="e001", errorMsg="invalid api key.")
        result, _ = _collect(SourceResponse(403, body, "application/json"))

        error = result.errors[0]
        self.assertEqual(
            (error.code, error.http_status, error.api_code),
            ("http_status", 403, "e001"),
        )
        self.assertEqual(error.error_identifier, "e001")
        self.assertEqual(error.error_identifier_source, "errorCode")
        self.assertNotIn("invalid api key.", repr(error))

    def test_error_code_precedes_result_code_and_result_code_is_fallback(self) -> None:
        cases = (
            ({"errorCode": "e003", "resultCode": "e001"}, "e003", "errorCode"),
            ({"resultCode": "e008"}, "e008", "resultCode"),
        )
        for values, expected_code, expected_source in cases:
            with self.subTest(values=values):
                body = _json_body(**values)
                result, _ = _collect(SourceResponse(400, body, "application/json"))
                error = result.errors[0]
                self.assertEqual(error.api_code, expected_code)
                self.assertEqual(error.error_identifier, expected_code)
                self.assertEqual(error.error_identifier_source, expected_source)

    def test_html_title_and_error_code_element_require_an_exact_code(self) -> None:
        bodies = (
            b"<html><head><title>e002</title></head></html>",
            b'<html><body><div id="error-code">e002</div></body></html>',
        )
        for body in bodies:
            with self.subTest(body=body):
                result, _ = _collect(
                    SourceResponse(400, body, "text/html; charset=utf-8")
                )
                error = result.errors[0]
                self.assertEqual(error.response_format, "html")
                self.assertEqual(error.content_type, "text/html")
                self.assertIsNone(error.api_code)
                self.assertEqual(error.error_identifier, "e002")
                self.assertEqual(error.error_identifier_source, "html")

    def test_response_formats_and_content_types_are_classified_without_body_text(self) -> None:
        cases = (
            (
                "json",
                b'{"errorCode":"e003"}',
                "application/json; charset=utf-8",
                "json",
                "application/json",
                "e003",
                "errorCode",
            ),
            (
                "html",
                b"<html><title>e002</title></html>",
                "text/html; charset=utf-8",
                "html",
                "text/html",
                "e002",
                "html",
            ),
            (
                "xml",
                b'<?xml version="1.0"?><error>virtual-detail</error>',
                "application/xml; charset=utf-8",
                "xml",
                "application/xml",
                None,
                None,
            ),
            (
                "xml-text-content-type",
                b"<error>virtual-detail</error>",
                "text/xml; charset=utf-8",
                "xml",
                "text/xml",
                None,
                None,
            ),
            (
                "text",
                f"virtual upstream text {PRIVATE_URL}".encode("utf-8"),
                "text/plain; charset=utf-8",
                "text",
                "text/plain",
                None,
                None,
            ),
            (
                "malformed-json",
                b'{"errorCode":"e003"',
                "application/json",
                "text",
                "application/json",
                None,
                None,
            ),
            (
                "empty",
                b"",
                "application/octet-stream",
                "empty",
                "application/octet-stream",
                None,
                None,
            ),
            (
                "binary",
                b"\x89PNG\r\n\x1a\n\x00\x01",
                "application/octet-stream",
                "binary",
                "application/octet-stream",
                None,
                None,
            ),
            (
                "other-content-type",
                b"opaque but printable",
                f"application/x-private; profile={PRIVATE_HEADER}",
                "text",
                "other",
                None,
                None,
            ),
            (
                "missing-content-type",
                b"plain response",
                None,
                "text",
                None,
                None,
                None,
            ),
        )
        for name, body, content_type, response_format, normalized_type, identifier, source in cases:
            with self.subTest(name=name):
                result, _ = _collect(SourceResponse(400, body, content_type))
                error = result.errors[0]
                self.assertEqual(error.response_format, response_format)
                self.assertEqual(error.content_type, normalized_type)
                self.assertEqual(error.response_bytes, len(body))
                self.assertEqual(error.response_sha256, sha256(body).hexdigest())
                self.assertEqual(error.error_identifier, identifier)
                self.assertEqual(error.error_identifier_source, source)
                serialized = repr(error)
                self.assertNotIn(PRIVATE_URL, serialized)
                self.assertNotIn(PRIVATE_HEADER, serialized)

    def test_arbitrary_html_title_code_and_url_are_not_used_or_exposed(self) -> None:
        body = (
            f"<html><head><title>{PRIVATE_TITLE} e004</title></head>"
            f'<body><div id="error-code">{PRIVATE_CODE} e005</div>{PRIVATE_URL}</body></html>'
        ).encode("utf-8")
        result, _ = _collect(SourceResponse(400, body, "text/html; charset=utf-8"))

        error = result.errors[0]
        self.assertEqual(error.response_format, "html")
        self.assertIsNone(error.api_code)
        self.assertIsNone(error.error_identifier)
        self.assertIsNone(error.error_identifier_source)
        serialized = repr(error)
        for private in (PRIVATE_TITLE, PRIVATE_CODE, PRIVATE_URL):
            self.assertNotIn(private, serialized)

    def test_large_json_body_is_fingerprinted_but_not_parsed_for_codes(self) -> None:
        body = (
            b'{"errorCode":"e001","errorMsg":"'
            + b"x" * 65536
            + b'"}'
        )
        self.assertGreater(len(body), 65536)
        result, _ = _collect(SourceResponse(400, body, "application/json"))

        error = result.errors[0]
        self.assertEqual(error.response_format, "text")
        self.assertEqual(error.response_bytes, len(body))
        self.assertEqual(error.response_sha256, sha256(body).hexdigest())
        self.assertIsNone(error.api_code)
        self.assertIsNone(error.error_identifier)
        self.assertIsNone(error.error_identifier_source)

    def test_unsafe_response_has_no_unconfirmed_response_metadata(self) -> None:
        result, _ = _collect(UnsafeSourceResponse())

        error = result.errors[0]
        self.assertEqual(
            (error.page, error.code, error.http_status, error.api_code),
            (1, "unsafe_response", None, None),
        )
        for field in RESPONSE_METADATA:
            with self.subTest(field=field):
                self.assertIsNone(getattr(error, field))


class ResponseDiagnosticOutputTests(TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data = Path(self.temporary.name)
        self.root = self.data / "processed" / "f08-youthcenter"
        self.status_path = self.root / "status.json"

    def _status(self) -> dict[str, object]:
        return json.loads(self.status_path.read_text(encoding="utf-8"))

    def _run_cli(self, fetcher: object) -> tuple[int, str, str]:
        stdout, stderr = StringIO(), StringIO()
        with ExitStack() as stack:
            stack.enter_context(
                patch.dict(os.environ, {"YOUTHCENTER_API_KEY": KEY}, clear=True)
            )
            stack.enter_context(
                patch("benefitfinder.jobs.__main__.YOUTHCENTER_POLICY_LIST", CONFIG)
            )
            stack.enter_context(
                patch(
                    "benefitfinder.jobs.__main__.YouthcenterHttpFetcher",
                    return_value=fetcher,
                )
            )
            stack.enter_context(redirect_stdout(stdout))
            stack.enter_context(redirect_stderr(stderr))
            code = main(["refresh-youthcenter", "--data-dir", str(self.data)])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_cli_json_error_code_matches_status_and_never_logs_message(self) -> None:
        body = _json_body(errorCode="e003")
        fetcher = _Fetcher(
            SourceResponse(400, body, "application/json; charset=utf-8")
        )

        code, stdout, stderr = self._run_cli(fetcher)

        self.assertEqual(code, 1)
        expected = [
            {
                "attempt": 1,
                "page": 1,
                "error_code": "http_status",
                "http_status": 400,
                "api_code": "e003",
                "response_format": "json",
                "content_type": "application/json",
                "response_bytes": len(body),
                "response_sha256": sha256(body).hexdigest(),
                "error_identifier": "e003",
                "error_identifier_source": "errorCode",
            }
        ]
        self.assertEqual(_diagnostics(stderr), expected)
        status = self._status()
        self.assertEqual(status["last_attempt"]["errors"], expected)
        self.assertEqual(set(status["last_attempt"]["errors"][0]), DIAGNOSTIC_KEYS)
        visible = stdout + stderr + json.dumps(status, ensure_ascii=False)
        for private in (PRIVATE_MESSAGE, PRIVATE_URL, "invalid api key.", "charset=utf-8"):
            self.assertNotIn(private, visible)

    def test_cli_html_arbitrary_text_and_header_parameters_are_omitted(self) -> None:
        body = (
            f"<html><head><title>{PRIVATE_TITLE} e004 {PRIVATE_URL}</title></head>"
            f"<body>{PRIVATE_CODE}</body></html>"
        ).encode("utf-8")
        fetcher = _Fetcher(
            SourceResponse(
                400,
                body,
                f"text/html; charset=utf-8; profile={PRIVATE_HEADER}",
            )
        )

        code, stdout, stderr = self._run_cli(fetcher)

        self.assertEqual(code, 1)
        expected = [
            {
                "attempt": 1,
                "page": 1,
                "error_code": "http_status",
                "http_status": 400,
                "api_code": None,
                "response_format": "html",
                "content_type": "text/html",
                "response_bytes": len(body),
                "response_sha256": sha256(body).hexdigest(),
                "error_identifier": None,
                "error_identifier_source": None,
            }
        ]
        self.assertEqual(_diagnostics(stderr), expected)
        status = self._status()
        self.assertEqual(status["last_attempt"]["errors"], expected)
        visible = stdout + stderr + json.dumps(status, ensure_ascii=False)
        for private in (PRIVATE_TITLE, PRIVATE_CODE, PRIVATE_URL, PRIVATE_HEADER):
            self.assertNotIn(private, visible)

    def test_failure_diagnostic_allows_only_well_formed_metadata(self) -> None:
        valid = CollectionError(
            page=3,
            code="parse_failed",
            detail=f"{PRIVATE_MESSAGE} {PRIVATE_URL}",
            snapshot_ref="private-snapshot-reference",
            external_id="private-external-id",
            http_status=403,
            api_code="e001",
            response_format="json",
            content_type="application/json",
            response_bytes=0,
            response_sha256="a" * 64,
            error_identifier="e001",
            error_identifier_source="errorCode",
        )
        expected = {
            "attempt": 1,
            "page": 3,
            "error_code": "parse_failed",
            "http_status": 403,
            "api_code": "e001",
            "response_format": "json",
            "content_type": "application/json",
            "response_bytes": 0,
            "response_sha256": "a" * 64,
            "error_identifier": "e001",
            "error_identifier_source": "errorCode",
        }
        diagnostic = _failure_diagnostic(valid, 1)
        self.assertEqual(diagnostic, expected)
        self.assertEqual(set(diagnostic), DIAGNOSTIC_KEYS)
        serialized = json.dumps(diagnostic, ensure_ascii=False)
        for private in (
            PRIVATE_MESSAGE,
            PRIVATE_URL,
            "private-snapshot-reference",
            "private-external-id",
        ):
            self.assertNotIn(private, serialized)

        invalid_values = (
            ("response_format", "json; charset=utf-8"),
            ("content_type", "application/json; charset=utf-8"),
            ("content_type", f"text/html\r\nX-Private: {PRIVATE_HEADER}"),
            ("response_bytes", -1),
            ("response_bytes", 2**63),
            ("response_bytes", True),
            ("response_sha256", "A" * 64),
            ("response_sha256", "a" * 63),
            ("response_sha256", f"{PRIVATE_CODE}" * 5),
            ("error_identifier", "E001"),
            ("error_identifier", "e001\n"),
            ("error_identifier", PRIVATE_CODE),
            ("error_identifier_source", "HTML"),
            ("error_identifier_source", PRIVATE_HEADER),
        )
        for field, value in invalid_values:
            with self.subTest(field=field, value=repr(value)):
                malicious = replace(valid, **{field: value})
                sanitized = _failure_diagnostic(malicious, 1)
                self.assertIsNone(sanitized[field])
                self.assertEqual(set(sanitized), DIAGNOSTIC_KEYS)
                self.assertNotIn(PRIVATE_HEADER, json.dumps(sanitized))
                self.assertNotIn(PRIVATE_CODE, json.dumps(sanitized))

    def test_malicious_collection_error_is_sanitized_in_stderr_and_status(self) -> None:
        error = CollectionError(
            page=1,
            code="parse_failed",
            detail=f"{PRIVATE_MESSAGE} {PRIVATE_URL}",
            snapshot_ref="private-snapshot-reference",
            external_id="private-external-id",
            http_status=400,
            api_code=f"e003 {PRIVATE_CODE}",
            response_format=f"json; {PRIVATE_HEADER}",
            content_type=f"application/json; {PRIVATE_HEADER}",
            response_bytes=2**63,
            response_sha256=PRIVATE_CODE,
            error_identifier=f"e003 {PRIVATE_CODE}",
            error_identifier_source=PRIVATE_HEADER,
        )
        result = CollectionResult(
            source_name="youthcenter",
            started_at=AT,
            records=(),
            changes=(),
            errors=(error,),
            complete=False,
        )
        stderr = StringIO()
        with redirect_stderr(stderr):
            self.assertFalse(
                run_youthcenter_refresh(
                    self.root,
                    lambda staging, previous: result,
                    lambda: AT,
                )
            )

        expected = [
            {
                "attempt": 1,
                "page": 1,
                "error_code": "parse_failed",
                "http_status": 400,
                "api_code": None,
                "response_format": None,
                "content_type": None,
                "response_bytes": None,
                "response_sha256": None,
                "error_identifier": None,
                "error_identifier_source": None,
            }
        ]
        self.assertEqual(_diagnostics(stderr.getvalue()), expected)
        status = self._status()
        self.assertEqual(status["last_attempt"]["errors"], expected)
        self.assertEqual(set(status["last_attempt"]["errors"][0]), DIAGNOSTIC_KEYS)
        visible = stderr.getvalue() + json.dumps(status, ensure_ascii=False)
        for private in (
            PRIVATE_MESSAGE,
            PRIVATE_URL,
            PRIVATE_CODE,
            PRIVATE_HEADER,
            "private-snapshot-reference",
            "private-external-id",
        ):
            self.assertNotIn(private, visible)
