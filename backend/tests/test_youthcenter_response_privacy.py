"""Credential-bearing source responses never enter snapshots, logs or retries."""

import io
import json
import os
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.parse import quote, quote_plus

from benefitfinder.application.collection import SourceRequest, SourceResponse, UnsafeSourceResponse
from benefitfinder.infrastructure.youthcenter_http import YouthcenterHttpFetcher
from benefitfinder.ingestion.collect import collect_youthcenter
from benefitfinder.ingestion.youthcenter import YOUTHCENTER_POLICY_LIST
from benefitfinder.jobs.__main__ import main
from benefitfinder.jobs.youthcenter_refresh import refresh_notification

KEY = 'fictional-key+/= "한글'
CONFIG = replace(YOUTHCENTER_POLICY_LIST, page_size=1)


def _page(policy_id: str, total: int = 1) -> SourceResponse:
    return SourceResponse(
        200,
        json.dumps({
            "resultCode": 200,
            "result": {
                "youthPolicyList": [{"plcyNo": policy_id, "plcyNm": "가상 공고"}],
                "pagging": {"totCount": total},
            },
        }).encode(),
        "application/json",
    )


class ResponsePrivacyTests(TestCase):
    def test_blocks_literal_url_and_json_echoes_before_http_retries(self) -> None:
        encoded = [
            KEY,
            quote(KEY, safe=""),
            quote_plus(KEY, safe=""),
            quote(KEY, safe="").replace("%2F", "%2f").replace("%3D", "%3d")
            .replace("%2B", "%2b").replace("%E", "%e").replace("%8C", "%8c")
            .replace("%ED", "%ed").replace("%9C", "%9c"),
            json.dumps(KEY)[1:-1],
            json.dumps(KEY, ensure_ascii=False)[1:-1].replace("/", r"\/"),
        ]
        # A provider may return an apparently successful response or echo a URL in an error.
        for status in (200, 403, 503):
            for value in encoded:
                with self.subTest(status=status, encoding=value):
                    fetcher = YouthcenterHttpFetcher(KEY, max_retries=3, sleep=lambda _: None)
                    response = SourceResponse(status, f"echo: {value}".encode(), "text/plain")
                    with patch.object(fetcher, "_send", return_value=response) as send:
                        with self.assertRaises(UnsafeSourceResponse) as raised:
                            fetcher.fetch(SourceRequest("https://example.invalid/policies", {}))
                    self.assertEqual(str(raised.exception), "source_response_contains_credential")
                    send.assert_called_once()

    def test_blocks_credential_in_content_type_metadata(self) -> None:
        fetcher = YouthcenterHttpFetcher(KEY)
        with patch.object(fetcher, "_send", return_value=SourceResponse(200, b"{}", KEY)):
            with self.assertRaises(UnsafeSourceResponse):
                fetcher.fetch(SourceRequest("https://example.invalid/policies", {}))

    def test_http_error_body_is_checked_before_snapshot_save(self) -> None:
        fetcher = YouthcenterHttpFetcher(KEY)
        error = HTTPError(
            "https://example.invalid/policies", 403, "Forbidden", {},
            io.BytesIO(f"request apiKeyNm={quote_plus(KEY)}".encode()),
        )
        snapshots, versions = Mock(), Mock()
        versions.latest_hashes.return_value = {}
        with patch("urllib.request.urlopen", side_effect=error) as open_url:
            result = collect_youthcenter(
                CONFIG, fetcher, snapshots, versions, lambda: datetime.now(timezone.utc),
            )
        self.assertFalse(result.complete)
        self.assertEqual([item.code for item in result.errors], ["unsafe_response"])
        self.assertIsNone(result.errors[0].snapshot_ref)
        snapshots.save.assert_not_called()
        versions.save_versions.assert_not_called()
        open_url.assert_called_once()

    def test_refresh_failure_preserves_previous_success_and_never_retries(self) -> None:
        with TemporaryDirectory() as temporary:
            data = Path(temporary)
            root = data / "processed" / "f08-youthcenter"
            output = io.StringIO()
            with (
                patch.dict(os.environ, {"YOUTHCENTER_API_KEY": KEY}),
                patch("benefitfinder.jobs.__main__.YOUTHCENTER_POLICY_LIST", CONFIG),
                patch.object(YouthcenterHttpFetcher, "_send", return_value=_page("V-OLD")),
                redirect_stdout(output), redirect_stderr(output),
            ):
                self.assertEqual(main(["refresh-youthcenter", "--data-dir", temporary]), 0)
            before = json.loads((root / "status.json").read_bytes())
            generation = root / "runs" / before["last_success"]["run_id"]
            previous_files = {p: (p.read_bytes(), p.stat().st_mtime_ns)
                              for p in generation.rglob("*") if p.is_file()}
            raw_before = set((data / "raw").rglob("*.meta.json"))
            started = datetime.now(timezone.utc)
            with (
                patch.dict(os.environ, {"YOUTHCENTER_API_KEY": KEY}),
                patch("benefitfinder.jobs.__main__.YOUTHCENTER_POLICY_LIST", CONFIG),
                patch.object(YouthcenterHttpFetcher, "_wait_turn"),
                patch.object(YouthcenterHttpFetcher, "_send", side_effect=[
                    _page("V-CHANGED", 2), SourceResponse(503, KEY.encode(), "text/plain"),
                ]) as send,
                redirect_stdout(output), redirect_stderr(output),
            ):
                self.assertEqual(main(["refresh-youthcenter", "--data-dir", temporary]), 1)
            state = json.loads((root / "status.json").read_bytes())
            self.assertEqual(state["last_success"], before["last_success"])
            self.assertEqual(state["last_attempt"]["attempts"], 1)
            self.assertEqual(state["last_attempt"]["error_codes"], ["unsafe_response"])
            self.assertEqual(send.call_count, 2)
            self.assertEqual(set((root / "runs").iterdir()), {generation})
            for path, contents in previous_files.items():
                self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), contents)
            notification = refresh_notification(root, 1, started)
            self.assertIn("unsafe_response", notification)
            self.assertNotIn(KEY, output.getvalue() + notification)
            for path in data.rglob("*"):
                if path.is_file():
                    self.assertNotIn(KEY.encode(), path.read_bytes())
            raw_after = set((data / "raw").rglob("*.meta.json"))
            # Only the safe first page can add a snapshot; the unsafe second page cannot.
            self.assertEqual(len(raw_after - raw_before), 1)
            for path in (data / "raw").rglob("*"):
                self.assertEqual(path.stat().st_mode & 0o777, 0o600 if path.is_file() else 0o700)

    def test_manual_collection_creates_private_raw_snapshots(self) -> None:
        with TemporaryDirectory() as temporary:
            output = io.StringIO()
            with (
                patch.dict(os.environ, {"YOUTHCENTER_API_KEY": KEY}),
                patch("benefitfinder.jobs.__main__.YOUTHCENTER_POLICY_LIST", CONFIG),
                patch.object(YouthcenterHttpFetcher, "_send", return_value=_page("V-SAFE")),
                redirect_stdout(output), redirect_stderr(output),
            ):
                self.assertEqual(main(["collect-youthcenter", "--data-dir", temporary]), 0)
            files = [p for p in (Path(temporary) / "raw").rglob("*") if p.is_file()]
            self.assertEqual(len(files), 2)
            for path in files:
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
