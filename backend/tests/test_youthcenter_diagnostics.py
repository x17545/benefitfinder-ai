"""Regression checks for bounded youthcenter collection diagnostics."""

from __future__ import annotations

import json
import os
from collections import deque
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from dataclasses import replace
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from benefitfinder.application.collection import (
    SourceResponse,
    UnsafeSourceResponse,
)
from benefitfinder.infrastructure.youthcenter_http import YouthcenterHttpFetcher
from benefitfinder.ingestion.collect import (
    CollectionError,
    CollectionResult,
    collect_youthcenter,
)
from benefitfinder.ingestion.youthcenter import YOUTHCENTER_POLICY_LIST
from benefitfinder.jobs.__main__ import main

AT = datetime(2026, 11, 17, 3, 0, tzinfo=timezone.utc)
CONFIG = replace(YOUTHCENTER_POLICY_LIST, page_size=1)
KEY = "fictional-diagnostic-key"
PRIVATE_URL = "https://private.invalid/path?token=private-token"
PRIVATE_DETAIL = "private upstream detail"
DIAGNOSTIC_PREFIX = "온통청년 수집 오류: "


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


def _page(
    policy_id: str | None = "V-A",
    *,
    total: int = 1,
    status: int = 200,
    result_code: object = 200,
) -> SourceResponse:
    policies = [] if policy_id is None else [
        {"plcyNo": policy_id, "plcyNm": "가상 공고"},
    ]
    body = {
        "resultCode": result_code,
        "result": {
            "youthPolicyList": policies,
            "pagging": {"totCount": total},
        },
    }
    return SourceResponse(
        status,
        json.dumps(body, ensure_ascii=False).encode("utf-8"),
        "application/json",
    )


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


def _invoke_jobs(
    arguments: list[str],
    fetcher: object,
    *,
    refresh_data: bool = False,
) -> tuple[int, str, str]:
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
        if refresh_data:
            stack.enter_context(
                patch(
                    "benefitfinder.jobs.__main__.refresh_ecos_rate_command",
                    return_value=0,
                )
            )
        stack.enter_context(redirect_stdout(stdout))
        stack.enter_context(redirect_stderr(stderr))
        code = main(arguments)
    return code, stdout.getvalue(), stderr.getvalue()


class CollectionDiagnosticTests(TestCase):
    def test_second_page_http_error_keeps_exact_page_status_and_api_code(self) -> None:
        result, fetcher = _collect(
            _page("V-A", total=2),
            SourceResponse(
                400,
                '{"resultCode":"e003","private":"요청 본문"}'.encode("utf-8"),
                "application/json",
            ),
        )

        self.assertFalse(result.complete)
        self.assertEqual(
            [request.params["pageNum"] for request in fetcher.requests],
            ["1", "2"],
        )
        self.assertEqual(
            [
                (error.page, error.code, error.http_status, error.api_code)
                for error in result.errors
            ],
            [(2, "http_status", 400, "e003")],
        )

    def test_http_200_api_error_and_malformed_body_are_parse_failures(self) -> None:
        cases = (
            (_page(None, total=0, result_code="e401"), "e401"),
            (
                SourceResponse(200, b"<html>not-json</html>", "text/html"),
                None,
            ),
        )
        for response, expected_api_code in cases:
            with self.subTest(expected_api_code=expected_api_code):
                result, _ = _collect(response)
                self.assertFalse(result.complete)
                self.assertEqual(
                    [
                        (error.page, error.code, error.http_status, error.api_code)
                        for error in result.errors
                    ],
                    [(1, "parse_failed", 200, expected_api_code)],
                )

    def test_non_json_http_error_has_unknown_api_code(self) -> None:
        result, _ = _collect(
            SourceResponse(403, b"plain upstream denial", "text/plain")
        )

        self.assertFalse(result.complete)
        self.assertEqual(
            [
                (error.page, error.code, error.http_status, error.api_code)
                for error in result.errors
            ],
            [(1, "http_status", 403, None)],
        )

    def test_rejects_arbitrary_and_unbounded_result_codes(self) -> None:
        invalid_codes = (
            "E003",
            "e03",
            "e0033",
            "4000",
            " e003",
            "e003\n",
            "private-source-code",
            "e" + "4" * 999,
            {"private": "nested-code"},
            99,
            1000,
        )
        for value in invalid_codes:
            with self.subTest(
                value_type=type(value).__name__,
                length=len(str(value)),
            ):
                body = json.dumps({"resultCode": value}).encode("utf-8")
                result, _ = _collect(
                    SourceResponse(400, body, "application/json")
                )
                error = result.errors[0]
                self.assertEqual(
                    (error.page, error.code, error.http_status, error.api_code),
                    (1, "http_status", 400, None),
                )
                self.assertEqual(error.detail, "400")
                self.assertNotIn("private-source-code", repr(error))

    def test_transport_and_unsafe_failures_have_null_unconfirmed_metadata(self) -> None:
        cases = (
            (
                ConnectionError(f"{PRIVATE_URL} {KEY} {PRIVATE_DETAIL}"),
                "fetch_failed",
            ),
            (UnsafeSourceResponse(), "unsafe_response"),
        )
        for outcome, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                result, _ = _collect(outcome)
                error = result.errors[0]
                self.assertEqual(
                    (error.page, error.code, error.http_status, error.api_code),
                    (1, expected_code, None, None),
                )
                self.assertNotIn(KEY, repr(error))
                self.assertNotIn(PRIVATE_URL, repr(error))
                self.assertNotIn(PRIVATE_DETAIL, repr(error))


class JobDiagnosticTests(TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        self.root = self.data / "processed" / "f08-youthcenter"
        self.status_path = self.root / "status.json"

    def _status(self) -> dict[str, object]:
        return json.loads(self.status_path.read_text(encoding="utf-8"))

    def _run_cli(
        self,
        arguments: list[str],
        fetcher: object,
        *,
        refresh_data: bool = False,
    ) -> tuple[int, str, str]:
        return _invoke_jobs(arguments, fetcher, refresh_data=refresh_data)

    def _tree(self, path: Path) -> dict[Path, tuple[bytes, int]]:
        return {
            item.relative_to(path): (item.read_bytes(), item.stat().st_mtime_ns)
            for item in path.rglob("*")
            if item.is_file()
        }

    def test_status_and_stderr_allow_only_diagnostic_metadata(self) -> None:
        errors = (
            CollectionError(
                page=2,
                code="conflicting_duplicate",
                detail=f"{PRIVATE_DETAIL}-body",
                snapshot_ref="private-snapshot-reference",
                external_id="private-external-id",
                http_status=418,
                api_code="private-api-code",
            ),
            CollectionError(
                page=3,
                code="parse_failed",
                detail=f"{PRIVATE_DETAIL}-parse",
                http_status=200,
                api_code="e403",
            ),
        )
        result = CollectionResult(
            source_name="youthcenter",
            started_at=AT,
            records=(),
            changes=(),
            errors=errors,
            complete=False,
        )
        stderr = StringIO()
        with redirect_stderr(stderr):
            from benefitfinder.jobs.youthcenter_refresh import run_youthcenter_refresh

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
                "page": 2,
                "error_code": "conflicting_duplicate",
                "http_status": 418,
                "api_code": None,
            },
            {
                "attempt": 1,
                "page": 3,
                "error_code": "parse_failed",
                "http_status": 200,
                "api_code": "e403",
            },
        ]
        self.assertEqual(_diagnostics(stderr.getvalue()), expected)
        status = self._status()
        self.assertEqual(status["last_attempt"]["errors"], expected)
        serialized = json.dumps(status, ensure_ascii=False) + stderr.getvalue()
        for private in (
            PRIVATE_DETAIL,
            "private-snapshot-reference",
            "private-external-id",
            "private-api-code",
        ):
            self.assertNotIn(private, serialized)
        self.assertEqual(
            set(status["last_attempt"]["errors"][0]),
            {"attempt", "page", "error_code", "http_status", "api_code"},
        )

    def test_retry_after_503_then_success_keeps_first_failure_and_old_generation(
        self,
    ) -> None:
        initial = _Fetcher(_page("V-OLD", total=1))
        code, _, _ = self._run_cli(
            ["refresh-youthcenter", "--data-dir", str(self.data)],
            initial,
        )
        self.assertEqual(code, 0)
        before = self._status()
        old_run = self.root / "runs" / before["last_success"]["run_id"]
        old_files = self._tree(old_run)

        retry = _Fetcher(
            _page("V-UNUSED", total=1, status=503, result_code="e503"),
            _page("V-NEW", total=1),
        )
        with patch("benefitfinder.jobs.youthcenter_refresh.sleep") as wait:
            code, _, stderr = self._run_cli(
                ["refresh-youthcenter", "--data-dir", str(self.data)],
                retry,
            )

        self.assertEqual(code, 0)
        wait.assert_called_once()
        self.assertEqual(
            [request.params["pageNum"] for request in retry.requests],
            ["1", "1"],
        )
        expected = [
            {
                "attempt": 1,
                "page": 1,
                "error_code": "http_status",
                "http_status": 503,
                "api_code": "e503",
            }
        ]
        self.assertEqual(_diagnostics(stderr), expected)
        after = self._status()
        self.assertEqual(after["last_attempt"]["status"], "success")
        self.assertEqual(after["last_attempt"]["attempts"], 2)
        self.assertEqual(after["last_attempt"]["errors"], expected)
        self.assertEqual(after["last_attempt"]["error_codes"], [])
        self.assertIsNotNone(after["last_success"])
        self.assertEqual(self._tree(old_run), old_files)

    def test_retry_exhaustion_preserves_last_success_and_accumulates_attempts(
        self,
    ) -> None:
        initial = _Fetcher(_page("V-OLD", total=1))
        code, _, _ = self._run_cli(
            ["refresh-youthcenter", "--data-dir", str(self.data)],
            initial,
        )
        self.assertEqual(code, 0)
        before = self._status()
        old_run = self.root / "runs" / before["last_success"]["run_id"]
        old_files = self._tree(old_run)

        retry = _Fetcher(
            _page("V-UNUSED", total=1, status=503, result_code="e503"),
            _page("V-UNUSED", total=1, status=503, result_code="e503"),
        )
        with patch("benefitfinder.jobs.youthcenter_refresh.sleep") as wait:
            code, _, stderr = self._run_cli(
                ["refresh-youthcenter", "--data-dir", str(self.data)],
                retry,
            )

        self.assertEqual(code, 1)
        wait.assert_called_once()
        expected = [
            {
                "attempt": 1,
                "page": 1,
                "error_code": "http_status",
                "http_status": 503,
                "api_code": "e503",
            },
            {
                "attempt": 2,
                "page": 1,
                "error_code": "http_status",
                "http_status": 503,
                "api_code": "e503",
            },
        ]
        self.assertEqual(_diagnostics(stderr), expected)
        after = self._status()
        self.assertEqual(after["last_success"], before["last_success"])
        self.assertEqual(after["last_attempt"]["status"], "failed")
        self.assertEqual(after["last_attempt"]["attempts"], 2)
        self.assertEqual(after["last_attempt"]["errors"], expected)
        self.assertEqual(self._tree(old_run), old_files)

    def test_refresh_cli_blocks_reflected_secret_without_output(self) -> None:
        reflected = (
            f"upstream echo url={PRIVATE_URL}&apiKeyNm={KEY}&detail={PRIVATE_DETAIL}"
        ).encode("utf-8")
        fetcher = YouthcenterHttpFetcher(
            KEY,
            min_interval_seconds=0,
            max_retries=0,
            sleep=lambda _: None,
        )
        with patch.object(
            fetcher,
            "_send",
            return_value=SourceResponse(403, reflected, "text/plain"),
        ):
            code, stdout, stderr = self._run_cli(
                ["refresh-youthcenter", "--data-dir", str(self.data)],
                fetcher,
            )

        self.assertEqual(code, 1)
        self.assertEqual(
            _diagnostics(stderr),
            [
                {
                    "attempt": 1,
                    "page": 1,
                    "error_code": "unsafe_response",
                    "http_status": None,
                    "api_code": None,
                }
            ],
        )
        status_text = self.status_path.read_text(encoding="utf-8")
        visible = stdout + stderr + status_text
        for private in (KEY, PRIVATE_URL, PRIVATE_DETAIL):
            self.assertNotIn(private, visible)

    def test_transport_exception_text_is_not_written_by_refresh_cli(self) -> None:
        secret_error = ConnectionError(
            f"{PRIVATE_URL} apiKeyNm={KEY} detail={PRIVATE_DETAIL}"
        )
        fetcher = _Fetcher(secret_error, secret_error)
        with patch("benefitfinder.jobs.youthcenter_refresh.sleep"):
            code, stdout, stderr = self._run_cli(
                ["refresh-youthcenter", "--data-dir", str(self.data)],
                fetcher,
            )

        self.assertEqual(code, 1)
        expected = [
            {
                "attempt": 1,
                "page": 1,
                "error_code": "fetch_failed",
                "http_status": None,
                "api_code": None,
            },
            {
                "attempt": 2,
                "page": 1,
                "error_code": "fetch_failed",
                "http_status": None,
                "api_code": None,
            },
        ]
        self.assertEqual(_diagnostics(stderr), expected)
        visible = stdout + stderr + self.status_path.read_text(encoding="utf-8")
        for private in (KEY, PRIVATE_URL, PRIVATE_DETAIL):
            self.assertNotIn(private, visible)

    def test_refresh_data_cli_uses_fake_fetcher_and_reports_diagnostic(self) -> None:
        fetcher = _Fetcher(
            SourceResponse(
                400,
                b'{"resultCode":"e003","private":"never logged"}',
                "application/json",
            )
        )
        code, _, stderr = self._run_cli(
            ["refresh-data", "--data-dir", str(self.data)],
            fetcher,
            refresh_data=True,
        )

        self.assertEqual(code, 1)
        expected = [
            {
                "attempt": 1,
                "page": 1,
                "error_code": "http_status",
                "http_status": 400,
                "api_code": "e003",
            }
        ]
        self.assertEqual(_diagnostics(stderr), expected)
        self.assertEqual(len(fetcher.requests), 1)
        self.assertEqual(self._status()["last_attempt"]["errors"], expected)
        daily = json.loads(
            (self.data / "processed" / "f08" / "status.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            daily["last_attempt"]["stages"]["youthcenter"]["status"],
            "failed",
        )
        self.assertEqual(
            daily["last_attempt"]["stages"]["ecos"]["status"],
            "success",
        )
        self.assertNotIn("never logged", stderr)
