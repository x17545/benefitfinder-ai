"""Offline checks for bounded collected-policy staging JSON."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import unittest
from unittest.mock import patch
from uuid import uuid4

from benefitfinder.application.collected_policies import CollectedPolicyBatch
from benefitfinder.application.collection import CollectedRecord
from benefitfinder.infrastructure.database.repositories import collected_policies as staging


AT = datetime(2026, 11, 17, 3, 0, tzinfo=timezone.utc)


def _record(external_id: str, fictional_text: str) -> CollectedRecord:
    material = f"fictional-material:{external_id}"
    return CollectedRecord(
        source_name="youthcenter",
        external_id=external_id,
        record_sha256=hashlib.sha256(material.encode("utf-8")).hexdigest(),
        snapshot_ref=f"fictional-snapshot://youthcenter/{external_id}",
        fetched_at=AT - timedelta(minutes=1),
        fields={"fictional_text": fictional_text},
    )


def _batch(records: tuple[CollectedRecord, ...]) -> CollectedPolicyBatch:
    return CollectedPolicyBatch("youthcenter", uuid4().hex, AT, records)


def _record_json(record: CollectedRecord) -> str:
    return json.dumps(
        {
            "source_name": record.source_name,
            "external_id": record.external_id,
            "record_sha256": record.record_sha256,
            "snapshot_ref": record.snapshot_ref,
            "fetched_at": record.fetched_at.astimezone(timezone.utc).isoformat(),
            "fields": record.fields,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


class CollectedPolicyStagingJsonTests(unittest.TestCase):
    def test_empty_batch_has_no_staging_statement(self) -> None:
        self.assertEqual(staging._incoming_record_chunks(_batch(())), ())

    def test_constants_and_200_row_limit_keep_last_record(self) -> None:
        self.assertEqual(staging._STAGING_MAX_RECORDS, 200)
        self.assertEqual(staging._STAGING_MAX_BYTES, 1024 * 1024)
        records = tuple(_record(f"row-{index:03d}", "가상") for index in range(201))

        chunks = staging._incoming_record_chunks(_batch(records))

        self.assertEqual([len(json.loads(chunk)) for chunk in chunks], [200, 1])
        self.assertEqual(json.loads(chunks[-1])[0]["external_id"], "row-200")

    def test_utf8_bytes_split_whole_records_below_actual_limit(self) -> None:
        filler = "가" * 180_000
        records = (
            _record("utf8-a", filler),
            _record("utf8-b", filler),
        )
        encoded_records = tuple(len(_record_json(record).encode("utf-8")) for record in records)
        self.assertTrue(all(size <= staging._STAGING_MAX_BYTES for size in encoded_records))
        self.assertGreater(
            2 + encoded_records[0] + 1 + encoded_records[1],
            staging._STAGING_MAX_BYTES,
        )

        chunks = staging._incoming_record_chunks(_batch(records))

        self.assertEqual(len(chunks), 2)
        self.assertEqual(
            [record["external_id"] for chunk in chunks for record in json.loads(chunk)],
            ["utf8-a", "utf8-b"],
        )
        self.assertTrue(
            all(len(chunk.encode("utf-8")) <= staging._STAGING_MAX_BYTES for chunk in chunks)
        )

    def test_exact_byte_boundary_counts_brackets_and_comma(self) -> None:
        records = (
            _record("boundary-a", "첫 번째 가상 자료"),
            _record("boundary-b", "두 번째 가상 자료"),
        )
        first, second = (_record_json(record) for record in records)
        exact_pair_bytes = 2 + len(first.encode("utf-8")) + 1 + len(second.encode("utf-8"))

        with patch.object(staging, "_STAGING_MAX_BYTES", exact_pair_bytes):
            chunks = staging._incoming_record_chunks(_batch(records))
            self.assertEqual(len(chunks), 1)
            self.assertEqual(len(chunks[0].encode("utf-8")), exact_pair_bytes)

        with patch.object(staging, "_STAGING_MAX_BYTES", exact_pair_bytes - 1):
            chunks = staging._incoming_record_chunks(_batch(records))
            self.assertEqual(len(chunks), 2)

    def test_oversized_singleton_is_kept_intact(self) -> None:
        record = _record("oversized", "가" * 400_000)

        chunks = staging._incoming_record_chunks(_batch((record,)))

        self.assertEqual(len(chunks), 1)
        self.assertGreater(len(chunks[0].encode("utf-8")), staging._STAGING_MAX_BYTES)
        self.assertEqual(json.loads(chunks[0])[0]["external_id"], "oversized")


if __name__ == "__main__":
    unittest.main()
