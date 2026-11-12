"""Offline collection/DB handoff, recovery and source-lock boundaries."""

from contextlib import redirect_stderr, redirect_stdout
import fcntl
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from benefitfinder.application.collected_policies import (
    CollectedPolicyBatch,
    CollectedPolicyStoreError,
    CollectionImportResult,
)
from benefitfinder.application.collection import SourceFetcher, SourceResponse
from benefitfinder.infrastructure.collection_files import read_collection_file
from benefitfinder.ingestion.collected_generations import load_youthcenter_generation
from benefitfinder.jobs.__main__ import main


class CollectedJobsTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        self.output = StringIO()
        for context in (
            patch.dict("os.environ", {"YOUTHCENTER_API_KEY": "fictional-key"}, clear=True),
            redirect_stdout(self.output),
            redirect_stderr(self.output),
        ):
            self.enterContext(context)
        self.fetcher = Mock(spec=SourceFetcher)
        self.set_response("가상 취업 공고")
        self.enterContext(
            patch("benefitfinder.jobs.__main__.YouthcenterHttpFetcher", return_value=self.fetcher)
        )
        self.store = self.enterContext(
            patch("benefitfinder.jobs.__main__.PostgresCollectedPolicies")
        )
        self.store.return_value.save.return_value = CollectionImportResult(1, 0, 0, 0)

    def set_response(self, title: str) -> None:
        body = {
            "resultCode": 200,
            "result": {
                "youthPolicyList": [{"plcyNo": "fictional-A", "plcyNm": title}],
                "pagging": {"totCount": 1},
            },
        }
        self.fetcher.fetch.return_value = SourceResponse(
            200, json.dumps(body).encode(), "application/json"
        )

    def command(self, name: str) -> int:
        return main([name, "--data-dir", str(self.data)])

    def batch(self) -> CollectedPolicyBatch:
        return load_youthcenter_generation(
            lambda relative, limit: read_collection_file(self.data, relative, limit)
        )

    def test_unconfigured_database_keeps_existing_file_only_collection(self) -> None:
        self.assertEqual(self.command("refresh-youthcenter"), 0)
        self.store.assert_not_called()
        self.assertEqual(len(self.batch().records), 1)
        self.assertIn("수집 DB 미설정", self.output.getvalue())

    def test_database_failure_is_retried_before_next_fetch_and_can_recover_without_api_key(
        self,
    ) -> None:
        with patch.dict("os.environ", {"COLLECTION_WRITE_DATABASE_URL": "PRIVATE DSN"}):
            self.store.return_value.save.side_effect = CollectedPolicyStoreError("PRIVATE SOURCE")
            self.assertEqual(self.command("refresh-youthcenter"), 1)
            selected = self.batch().run_id
            self.assertEqual(self.command("refresh-youthcenter"), 1)
            self.assertEqual(self.batch().run_id, selected)
            self.assertEqual(self.fetcher.fetch.call_count, 1)
            self.store.return_value.save.side_effect = None
            with patch.dict(
                "os.environ", {"COLLECTION_WRITE_DATABASE_URL": "PRIVATE DSN"}, clear=True
            ):
                self.assertEqual(self.command("ingest-youthcenter"), 0)
            batches = [call.args[0] for call in self.store.return_value.save.call_args_list]
            self.assertEqual([batch.run_id for batch in batches], [selected] * 3)
            self.assertEqual(self.fetcher.fetch.call_count, 1)
            self.set_response("가상 변경 공고")
            self.assertEqual(self.command("refresh-youthcenter"), 0)
            self.assertNotEqual(self.batch().run_id, selected)
            self.assertEqual(self.fetcher.fetch.call_count, 2)
        self.assertNotIn("PRIVATE", self.output.getvalue())

    def test_partial_collection_never_reaches_database(self) -> None:
        self.fetcher.fetch.return_value = SourceResponse(400, b"fictional failure", None)
        with patch.dict("os.environ", {"COLLECTION_WRITE_DATABASE_URL": "fictional DSN"}):
            self.assertEqual(self.command("refresh-youthcenter"), 1)
        self.store.assert_not_called()

    def test_source_lock_is_held_during_both_previous_and_new_generation_handoffs(self) -> None:
        def save(batch: CollectedPolicyBatch) -> CollectionImportResult:
            lock_path = self.data / "processed/f08-youthcenter/.refresh-lock"
            with lock_path.open("rb") as handle:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return CollectionImportResult(1, 0, 0, 0)

        self.store.return_value.save.side_effect = save
        with patch.dict("os.environ", {"COLLECTION_WRITE_DATABASE_URL": "fictional DSN"}):
            self.assertEqual(self.command("refresh-youthcenter"), 0)
            self.assertEqual(self.command("refresh-youthcenter"), 0)
        self.assertEqual(self.store.return_value.save.call_count, 3)

    def test_corrupt_original_blocks_handoff_and_new_collection(self) -> None:
        self.assertEqual(self.command("refresh-youthcenter"), 0)
        batch = self.batch()
        raw = self.data / "raw/f08-youthcenter" / batch.records[0].snapshot_ref
        raw.write_bytes(raw.read_bytes() + b" ")
        with patch.dict("os.environ", {"COLLECTION_WRITE_DATABASE_URL": "fictional DSN"}):
            self.assertEqual(self.command("refresh-youthcenter"), 1)
        self.store.assert_not_called()
        self.assertEqual(self.fetcher.fetch.call_count, 1)

    def test_missing_or_empty_database_configuration_is_not_a_successful_replay(self) -> None:
        self.assertEqual(self.command("ingest-youthcenter"), 2)
        with patch.dict("os.environ", {"COLLECTION_WRITE_DATABASE_URL": " "}):
            self.assertEqual(self.command("ingest-youthcenter"), 2)
            self.assertEqual(self.command("refresh-youthcenter"), 2)
        self.store.assert_not_called()
        self.fetcher.fetch.assert_not_called()
