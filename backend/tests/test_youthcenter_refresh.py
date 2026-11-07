"""Run the F08 CLI with synthetic pages and real local storage."""

import json
import os
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from benefitfinder.application.collection import SourceFetcher, SourceResponse
from benefitfinder.ingestion.youthcenter import YOUTHCENTER_POLICY_LIST
from benefitfinder.jobs.__main__ import main

CONFIG = replace(YOUTHCENTER_POLICY_LIST, page_size=1)


def _page(policy_id: str | None, name: str = "가상 공고", total: int = 2) -> SourceResponse:
    policies = [{"plcyNo": policy_id, "plcyNm": name}] if policy_id else []
    body = {
        "resultCode": 200,
        "result": {"youthPolicyList": policies, "pagging": {"totCount": total}},
    }
    return SourceResponse(200, json.dumps(body).encode("utf-8"), "application/json")


class RefreshCommandTests(TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        self.root = self.data / "processed" / "f08-youthcenter"

    def _run(self, *pages: SourceResponse) -> tuple[int, list[str]]:
        fetcher = Mock(spec=SourceFetcher)
        fetcher.fetch.side_effect = pages
        with (
            patch.dict(os.environ, {"YOUTHCENTER_API_KEY": "virtual-refresh-key"}),
            patch("benefitfinder.jobs.__main__.YOUTHCENTER_POLICY_LIST", CONFIG),
            patch("benefitfinder.jobs.__main__.YouthcenterHttpFetcher", return_value=fetcher),
            redirect_stdout(StringIO()),
            redirect_stderr(StringIO()),
        ):
            code = main(["refresh-youthcenter", "--data-dir", str(self.data)])
        return code, [call.args[0].params["pageNum"] for call in fetcher.fetch.call_args_list]

    def _status(self) -> dict[str, object]:
        return json.loads((self.root / "status.json").read_bytes())

    def _generation(self, status: dict[str, object]) -> Path:
        return self.root / "runs" / status["last_success"]["run_id"] / "youthcenter"

    def test_second_page_failure_does_not_change_last_success_or_its_files(self) -> None:
        self.assertEqual(self._run(_page("V-A"), _page("V-B")), (0, ["1", "2"]))
        before = self._status()
        published = self._generation(before)
        files = {
            path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in published.iterdir()
        }
        self.assertEqual(set(files), {"records.json", "records.csv", "fields.csv", "changes.jsonl"})
        rejected = SourceResponse(400, b"virtual response", None)
        self.assertEqual(self._run(_page("V-A", "실패한 시도의 변경"), rejected), (1, ["1", "2"]))
        after = self._status()
        self.assertEqual(after["last_success"], before["last_success"])
        self.assertEqual(after["last_attempt"]["status"], "failed")
        self.assertEqual(after["last_attempt"]["attempts"], 1)
        self.assertEqual(after["last_attempt"]["error_codes"], ["http_status"])
        self.assertEqual(set((self.root / "runs").iterdir()), {published.parent})
        self.assertEqual(
            {
                path.name: (path.read_bytes(), path.stat().st_mtime_ns)
                for path in published.iterdir()
            },
            files,
        )

        self.assertEqual(self._run(_page("V-A", "확인된 변경", total=1)), (0, ["1"]))
        latest = self._status()
        self.assertNotEqual(latest["last_success"], before["last_success"])
        self.assertEqual(latest["last_attempt"]["changes"], {"changed": 1, "missing": 1})
        records = json.loads((self._generation(latest) / "records.json").read_bytes())
        self.assertEqual(records["V-A"]["fields"]["plcyNm"], "확인된 변경")
        self.assertIsNotNone(records["V-B"]["missing_detected_at"])

    def test_transient_failure_restarts_from_page_one_and_only_selects_complete_run(self) -> None:
        unavailable = SourceResponse(503, b"virtual failure", None)
        with patch("benefitfinder.jobs.youthcenter_refresh.sleep") as wait:
            result = self._run(_page("V-X"), unavailable, _page("V-A"), _page("V-B"))
        self.assertEqual(result, (0, ["1", "2", "1", "2"]))
        wait.assert_called_once()
        status = self._status()
        self.assertEqual(status["last_attempt"]["attempts"], 2)
        self.assertEqual(status["last_attempt"]["changes"], {"new": 2})
        records = json.loads((self._generation(status) / "records.json").read_bytes())
        self.assertEqual(set(records), {"V-A", "V-B"})
        self.assertEqual(len(list((self.root / "runs").iterdir())), 1)

    def test_first_failure_has_no_success_and_empty_complete_run_has_readable_files(self) -> None:
        self.assertEqual(self._run(SourceResponse(400, b"virtual response", None)), (1, ["1"]))
        self.assertIsNone(self._status()["last_success"])
        self.assertEqual(list((self.root / "runs").iterdir()), [])
        self.assertEqual(self._run(_page(None, total=0)), (0, ["1"]))
        status = self._status()
        self.assertEqual(status["last_success"]["record_count"], 0)
        latest = self._generation(status)
        self.assertEqual(json.loads((latest / "records.json").read_bytes()), {})
        self.assertTrue((latest / "records.csv").is_file())
        self.assertTrue((latest / "fields.csv").is_file())

    def test_csv_failure_does_not_select_a_partially_written_run(self) -> None:
        self.assertEqual(self._run(_page("V-A", total=1)), (0, ["1"]))
        previous = self._status()["last_success"]
        with patch("benefitfinder.jobs.__main__.write_field_summary_csv", side_effect=OSError):
            self.assertEqual(self._run(_page("V-B", total=1)), (1, ["1"]))
        status = self._status()
        self.assertEqual(status["last_success"], previous)
        self.assertEqual(status["last_attempt"]["error_codes"], ["refresh_attempt_failed"])
        self.assertEqual(len(list((self.root / "runs").iterdir())), 1)
