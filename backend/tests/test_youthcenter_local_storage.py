"""온통청년 HTTP 어댑터·로컬 파일 저장소·실행 명령 테스트.

외부 네트워크 없이 127.0.0.1의 임시 서버와 임시 폴더, 가상 값만 사용한다.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

from benefitfinder.application.collection import (
    ChangeKind,
    CollectedRecord,
    RawSnapshot,
    RecordChange,
    SourceRequest,
    SourceResponse,
)
from benefitfinder.infrastructure.local_files import (
    JsonFileSnapshotStore,
    JsonFileVersionStore,
    write_field_summary_csv,
    write_records_csv,
)
from benefitfinder.infrastructure.youthcenter_http import YouthcenterHttpFetcher
from benefitfinder.ingestion.collect import collect_youthcenter
from benefitfinder.ingestion.youthcenter import SOURCE_NAME, YOUTHCENTER_POLICY_LIST
from benefitfinder.jobs.__main__ import main as jobs_main

KST = timezone(timedelta(hours=9))
FAKE_KEY = "virtual-test-key-0000"


class RecordingHandler(BaseHTTPRequestHandler):
    seen: list[tuple[str, dict[str, list[str]], str | None]] = []
    status = 200

    def do_GET(self) -> None:  # noqa: N802 - http.server 규약
        query = parse_qs(urlparse(self.path).query)
        RecordingHandler.seen.append((self.path, query, self.headers.get("Accept")))
        body = b'{"resultCode": 200}' if self.status == 200 else b"busy"
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        return


class FetcherTests(unittest.TestCase):
    def setUp(self) -> None:
        RecordingHandler.seen = []
        RecordingHandler.status = 200
        self.server = HTTPServer(("127.0.0.1", 0), RecordingHandler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/getPlcy"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def fetcher(self, **kwargs: object) -> YouthcenterHttpFetcher:
        return YouthcenterHttpFetcher(
            FAKE_KEY, require_https=False, min_interval_seconds=0, **kwargs
        )

    def test_adds_key_only_at_request_time(self) -> None:
        request = SourceRequest(self.url, {"pageNum": "1", "rtnType": "json"})
        response = self.fetcher().fetch(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body, b'{"resultCode": 200}')
        _, query, accept = RecordingHandler.seen[0]
        self.assertEqual(query["apiKeyNm"], [FAKE_KEY])
        self.assertEqual(query["pageNum"], ["1"])
        self.assertEqual(accept, "application/json")
        self.assertNotIn("apiKeyNm", request.params)

    def test_http_error_is_returned_not_raised(self) -> None:
        RecordingHandler.status = 503
        response = self.fetcher().fetch(SourceRequest(self.url, {}))
        self.assertEqual((response.status_code, response.body), (503, b"busy"))

    def test_guards(self) -> None:
        with self.assertRaises(ValueError):
            YouthcenterHttpFetcher("  ")
        with self.assertRaises(ValueError):
            self.fetcher().fetch(SourceRequest(self.url, {"apiKeyNm": "x"}))
        with self.assertRaises(ValueError):
            YouthcenterHttpFetcher(FAKE_KEY).fetch(SourceRequest(self.url, {}))
        self.assertNotIn(FAKE_KEY, repr(self.fetcher()))

    def test_waits_between_requests(self) -> None:
        clock = iter([10.0, 10.1, 10.2])
        waits: list[float] = []
        fetcher = YouthcenterHttpFetcher(
            FAKE_KEY, require_https=False, min_interval_seconds=1.0,
            sleep=waits.append, monotonic=lambda: next(clock),
        )
        fetcher.fetch(SourceRequest(self.url, {}))
        fetcher.fetch(SourceRequest(self.url, {}))
        self.assertEqual(len(waits), 1)
        self.assertAlmostEqual(waits[0], 0.9)


def record(external_id: str, digest: str, name: str = "가상 정책") -> CollectedRecord:
    return CollectedRecord(
        source_name=SOURCE_NAME,
        external_id=external_id,
        record_sha256=digest,
        snapshot_ref="youthcenter/snapshots/x.json",
        fetched_at=datetime(2026, 10, 30, 3, 0, tzinfo=KST),
        fields={"plcyNo": external_id, "plcyNm": name},
    )


def change(external_id: str, kind: ChangeKind) -> RecordChange:
    return RecordChange(SOURCE_NAME, external_id, kind, None, None, None)


class LocalFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_snapshot_keeps_body_and_metadata_without_key(self) -> None:
        snapshot = RawSnapshot(
            source_name=SOURCE_NAME,
            request=SourceRequest("https://api.example.invalid", {"pageNum": "2"}),
            fetched_at=datetime(2026, 10, 30, 3, 0, tzinfo=KST),
            status_code=200,
            content_type="application/json",
            content_sha256="a" * 64,
            body="{\"가상\": 1}".encode("utf-8"),
        )
        ref = JsonFileSnapshotStore(self.root).save(snapshot)
        self.assertTrue(ref.startswith("youthcenter/snapshots/"))
        self.assertIn("-p2-", ref)
        self.assertEqual((self.root / ref).read_bytes(), snapshot.body)
        meta_path = next((self.root / "youthcenter" / "snapshots").glob("*.meta.json"))
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        self.assertEqual(meta["request_params"], {"pageNum": "2"})
        self.assertEqual(meta["fetched_at"], "2026-10-30T03:00:00+09:00")

    def test_version_store_tracks_latest_and_missing(self) -> None:
        store = JsonFileVersionStore(self.root)
        store.save_versions(
            [record("P-A", "h1"), record("P-B", "h2")],
            [change("P-A", ChangeKind.NEW), change("P-B", ChangeKind.NEW)],
        )
        self.assertEqual(store.latest_hashes(SOURCE_NAME), {"P-A": "h1", "P-B": "h2"})

        store.save_versions(
            [record("P-A", "h1")],
            [change("P-A", ChangeKind.UNCHANGED), change("P-B", ChangeKind.MISSING)],
        )
        self.assertEqual(store.latest_hashes(SOURCE_NAME), {"P-A": "h1"})
        saved = store.load_records(SOURCE_NAME)
        self.assertIsNotNone(saved["P-B"]["missing_detected_at"])
        lines = store.changes_path(SOURCE_NAME).read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(line)["kind"] for line in lines], ["new", "new", "missing"])

    def test_csv_exports_open_in_excel(self) -> None:
        records = {
            "P-A": {"last_checked_at": "t", "fields": {"plcyNo": "P-A", "plcyNm": "가상 정책"}},
            "P-B": {"last_checked_at": "t", "fields": {"plcyNo": "P-B", "plcyNm": ""}},
        }
        records_csv, fields_csv = self.root / "records.csv", self.root / "fields.csv"
        write_records_csv(records, records_csv)
        write_field_summary_csv(records, fields_csv)
        self.assertTrue(records_csv.read_bytes().startswith(b"\xef\xbb\xbf"))
        rows = records_csv.read_text(encoding="utf-8-sig").splitlines()
        self.assertEqual(rows[0], "external_id,last_checked_at,missing_detected_at,plcyNo,plcyNm")
        summary = fields_csv.read_text(encoding="utf-8-sig").splitlines()
        self.assertIn("plcyNm,1,2,1,가상 정책", summary)

    def test_collect_twice_with_file_stores(self) -> None:
        def body(views: str) -> bytes:
            document = {
                "resultCode": 200,
                "result": {
                    "pagging": {"totCount": 1},
                    "youthPolicyList": [
                        {"plcyNo": "V-1", "plcyNm": "가상", "inqCnt": views,
                         "operInstPicNm": "가상 담당자"}
                    ],
                },
            }
            return json.dumps(document, ensure_ascii=False).encode("utf-8")

        class OnePage:
            def __init__(self, payload: bytes) -> None:
                self.payload = payload

            def fetch(self, request: SourceRequest) -> SourceResponse:
                return SourceResponse(200, self.payload, "application/json")

        versions = JsonFileVersionStore(self.root / "processed")
        snapshots = JsonFileSnapshotStore(self.root / "raw")
        clock = lambda: datetime(2026, 10, 30, 3, 0, tzinfo=KST)  # noqa: E731
        first = collect_youthcenter(
            YOUTHCENTER_POLICY_LIST, OnePage(body("1")), snapshots, versions, clock
        )
        second = collect_youthcenter(
            YOUTHCENTER_POLICY_LIST, OnePage(body("50")), snapshots, versions, clock
        )
        self.assertEqual(first.changes[0].kind, ChangeKind.NEW)
        self.assertEqual(second.changes[0].kind, ChangeKind.UNCHANGED)
        saved = versions.load_records(SOURCE_NAME)["V-1"]["fields"]
        self.assertNotIn("operInstPicNm", saved)


class JobsCommandTests(unittest.TestCase):
    def test_missing_key_stops_before_any_request(self) -> None:
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {}, clear=True):
            with contextlib.redirect_stderr(stderr):
                code = jobs_main(["collect-youthcenter", "--data-dir", tmp])
            self.assertEqual(code, 2)
            self.assertEqual(list(Path(tmp).iterdir()), [])
        self.assertIn("YOUTHCENTER_API_KEY", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
