"""Synthetic complete generations and tampered source/provenance boundaries."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from benefitfinder.application.collection import CollectedRecord, RawSnapshot
from benefitfinder.application.collected_policies import CollectedPolicyBatch
from benefitfinder.infrastructure.collection_files import read_collection_file
from benefitfinder.infrastructure.local_files import JsonFileSnapshotStore, JsonFileVersionStore
from benefitfinder.ingestion.collected_generations import (
    CollectedGenerationError,
    load_youthcenter_generation,
)
from benefitfinder.ingestion.versioning import diff_records, record_sha256, sha256_hex
from benefitfinder.ingestion.youthcenter import (
    YOUTHCENTER_POLICY_LIST as CONFIG,
    hashed_fields,
    page_request,
    stored_fields,
)

AT = datetime(2026, 11, 12, tzinfo=timezone.utc)
RUN = "a" * 32


class CollectedGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        self.root = self.data / "processed/f08-youthcenter"
        self.status_path = self.root / "status.json"
        self.record_path = self.root / "runs" / RUN / "youthcenter/records.json"
        self.prepare()

    def prepare(self, rows: list[dict] | None = None) -> None:
        if rows is None:
            rows = [
                {
                    "plcyNo": "fictional-A",
                    "plcyNm": "가상 취업 공고",
                    "inqCnt": 3,
                    "sprvsnInstPicNm": "가상 담당자",
                }
            ]
        body = json.dumps(
            {
                "resultCode": 200,
                "result": {
                    "youthPolicyList": rows,
                    "pagging": {"totCount": len(rows)},
                },
            },
            ensure_ascii=False,
        ).encode()
        reference = JsonFileSnapshotStore(self.data / "raw/f08-youthcenter").save(
            RawSnapshot(
                "youthcenter",
                page_request(CONFIG, 1),
                AT,
                200,
                "application/json",
                sha256_hex(body),
                body,
            )
        )
        self.snapshot_path = self.data / "raw/f08-youthcenter" / reference
        self.metadata_path = self.snapshot_path.with_suffix(".meta.json")
        records = tuple(
            CollectedRecord(
                "youthcenter",
                row["plcyNo"],
                record_sha256(hashed_fields(CONFIG, row)),
                reference,
                AT,
                stored_fields(CONFIG, row),
            )
            for row in rows
        )
        versions = JsonFileVersionStore(self.root / "runs" / RUN)
        self.record_path.parent.mkdir(parents=True, exist_ok=True)
        self.record_path.write_text("{}")
        versions.save_versions(
            records,
            diff_records("youthcenter", {}, records, complete=True),
            checked_at=AT,
        )
        self.status_path.write_text(
            json.dumps(
                {
                    "format_version": 1,
                    "last_success": {
                        "run_id": RUN,
                        "completed_at": (AT + timedelta(seconds=1)).isoformat(),
                        "record_count": len(rows),
                    },
                    "last_attempt": {"status": "success"},
                }
            )
        )

    def load(self) -> CollectedPolicyBatch:
        return load_youthcenter_generation(
            lambda relative, limit: read_collection_file(self.data, relative, limit)
        )

    def assert_invalid(self) -> None:
        with self.assertRaisesRegex(CollectedGenerationError, "^invalid_collected_generation$"):
            self.load()

    def test_complete_generation_preserves_raw_fields_hash_time_and_reference(self) -> None:
        batch = self.load()
        self.assertEqual(batch.run_id, RUN)
        self.assertEqual(batch.completed_at, AT + timedelta(seconds=1))
        self.assertEqual(len(batch.records), 1)
        record = batch.records[0]
        self.assertEqual(record.external_id, "fictional-A")
        self.assertEqual(record.fetched_at, AT)
        self.assertEqual(record.fields["inqCnt"], 3)
        self.assertNotIn("sprvsnInstPicNm", record.fields)
        self.assertEqual(record.record_sha256, record_sha256(hashed_fields(CONFIG, record.fields)))
        self.assertEqual(batch.fingerprint(), self.load().fingerprint())

    def test_last_complete_generation_remains_importable_after_new_collection_failure(self) -> None:
        status = json.loads(self.status_path.read_text())
        status["last_attempt"] = {"status": "failed", "error_codes": ["fetch_failed"]}
        self.status_path.write_text(json.dumps(status))
        self.assertEqual(self.load().run_id, RUN)

    def test_complete_empty_generation_and_historical_missing_entries(self) -> None:
        self.prepare([])
        self.assertEqual(self.load().records, ())
        self.prepare()
        entries = json.loads(self.record_path.read_text())
        historical = dict(entries["fictional-A"], missing_detected_at=AT.isoformat())
        entries["fictional-withdrawn"] = historical
        self.record_path.write_text(json.dumps(entries))
        self.assertEqual(len(self.load().records), 1)

    def test_incomplete_or_invalid_pointer_and_counts_are_rejected(self) -> None:
        original = json.loads(self.status_path.read_text())
        variants = [dict(original, format_version=True), dict(original, last_success=None)]
        for key, value in (
            ("run_id", "../PRIVATE"),
            ("record_count", True),
            ("record_count", 0),
            ("completed_at", "2026-11-12T00:00:00"),
        ):
            variants.append(dict(original, last_success={**original["last_success"], key: value}))
        for status in variants:
            with self.subTest(status=status):
                self.status_path.write_text(json.dumps(status))
                self.assert_invalid()

    def test_changed_record_cannot_pass_by_rehashing_without_matching_original_response(
        self,
    ) -> None:
        entries = json.loads(self.record_path.read_text())
        entry = entries["fictional-A"]
        entry["fields"]["plcyNm"] = "PRIVATE invented policy"
        self.record_path.write_text(json.dumps(entries))
        self.assert_invalid()
        entry["record_sha256"] = record_sha256(hashed_fields(CONFIG, entry["fields"]))
        self.record_path.write_text(json.dumps(entries))
        self.assert_invalid()

    def test_changed_original_response_and_invalid_source_metadata_are_rejected(self) -> None:
        raw = self.snapshot_path.read_bytes()
        self.snapshot_path.write_bytes(raw + b" ")
        self.assert_invalid()
        self.snapshot_path.write_bytes(raw)
        original = json.loads(self.metadata_path.read_text())
        for key, value in (
            ("request_url", "https://invalid.example/PRIVATE"),
            ("content_sha256", "f" * 64),
            ("status_code", True),
            ("fetched_at", (AT - timedelta(days=1)).isoformat()),
            ("request_params", {"pageNum": "1", "apiKeyNm": "PRIVATE"}),
        ):
            with self.subTest(key=key):
                self.metadata_path.write_text(json.dumps(dict(original, **{key: value})))
                self.assert_invalid()

    def test_missing_source_file_and_traversal_references_are_rejected(self) -> None:
        self.snapshot_path.unlink()
        self.assert_invalid()
        self.prepare()
        entries = json.loads(self.record_path.read_text())
        entries["fictional-A"]["snapshot_ref"] = "youthcenter/snapshots/../../PRIVATE.json"
        self.record_path.write_text(json.dumps(entries))
        self.assert_invalid()

    def test_future_or_naive_record_checks_and_material_id_mismatch_are_rejected(self) -> None:
        original = json.loads(self.record_path.read_text())
        for key, value in (
            ("last_checked_at", (AT + timedelta(days=1)).isoformat()),
            ("last_checked_at", "2026-11-12T00:00:00"),
            ("fields", {"plcyNo": "fictional-other"}),
        ):
            with self.subTest(key=key, value=value):
                entries = {"fictional-A": dict(original["fictional-A"], **{key: value})}
                self.record_path.write_text(json.dumps(entries))
                self.assert_invalid()

    def test_duplicate_keys_nonfinite_values_and_oversized_status_are_rejected(self) -> None:
        for text in (
            '{"format_version":1,"format_version":1}',
            '{"format_version":NaN}',
            "x" * 65_537,
        ):
            with self.subTest(length=len(text)):
                self.status_path.write_text(text)
                self.assert_invalid()

    def test_file_adapter_rejects_symlink_escape_and_enforces_read_limit(self) -> None:
        with TemporaryDirectory() as outside:
            target = Path(outside) / "fictional-original"
            target.write_text("fictional test only")
            (self.data / "escape").symlink_to(target)
            with self.assertRaisesRegex(ValueError, "^invalid_collection_file$"):
                read_collection_file(self.data, "escape", 100)
        with self.assertRaisesRegex(ValueError, "^collection_file_too_large$"):
            read_collection_file(self.data, "processed/f08-youthcenter/status.json", 3)

    def test_batch_rejects_duplicate_ids_invalid_times_hashes_and_non_json_fields(self) -> None:
        batch = self.load()
        record = batch.records[0]
        variants = [
            {"source_name": "other"},
            {"run_id": "../PRIVATE"},
            {"completed_at": AT.replace(tzinfo=None)},
            {"records": (record, record)},
            {"records": (replace(record, record_sha256="invalid"),)},
            {"records": (replace(record, fetched_at=AT + timedelta(days=1)),)},
            {"records": (replace(record, fields={"amount": float("nan")}),)},
        ]
        for values in variants:
            with self.subTest(keys=tuple(values)):
                with self.assertRaisesRegex(ValueError, "^invalid_collected_policy_batch$"):
                    replace(batch, **values)
