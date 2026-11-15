"""A complete source generation for offline storage, never a published benefit."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Protocol

from benefitfinder.application.collection import CollectedRecord

COLLECTED_POLICY_SOURCES = ('gov24', 'official_documents', 'youthcenter')


class CollectedPolicyStoreError(RuntimeError):
    """Fixed storage error that cannot expose source contents or credentials."""


@dataclass(frozen=True)
class CollectedPolicyBatch:
    source_name: str
    run_id: str
    completed_at: datetime
    records: tuple[CollectedRecord, ...]

    def __post_init__(self) -> None:
        try:
            if (
                self.source_name not in COLLECTED_POLICY_SOURCES
                or re.fullmatch(r"[0-9a-f]{32}", self.run_id) is None
                or self.completed_at.utcoffset() is None
                or not isinstance(self.records, tuple)
                or len(self.records) > 50_000
            ):
                raise ValueError
            seen = set()
            for record in self.records:
                if (
                    record.source_name != self.source_name
                    or not isinstance(record.external_id, str)
                    or not 1 <= len(record.external_id) <= 200
                    or not record.external_id.strip()
                    or "\0" in record.external_id
                    or record.external_id in seen
                    or re.fullmatch(r"[0-9a-f]{64}", record.record_sha256) is None
                    or not isinstance(record.snapshot_ref, str)
                    or not record.snapshot_ref
                    or record.fetched_at.utcoffset() is None
                    or record.fetched_at > self.completed_at
                    or not isinstance(record.fields, dict)
                ):
                    raise ValueError
                seen.add(record.external_id)
            # Check JSON representability before any transaction is opened.
            self.fingerprint()
        except (AttributeError, TypeError, ValueError, OverflowError, RecursionError):
            raise ValueError("invalid_collected_policy_batch") from None

    def fingerprint(self) -> str:
        """Bind a replay token to the exact generation, not just material hashes."""
        payload = {
            "source_name": self.source_name,
            "run_id": self.run_id,
            "completed_at": self.completed_at.astimezone(timezone.utc).isoformat(),
            "records": [
                {
                    "external_id": record.external_id,
                    "record_sha256": record.record_sha256,
                    "snapshot_ref": record.snapshot_ref,
                    "fetched_at": record.fetched_at.astimezone(timezone.utc).isoformat(),
                    "fields": record.fields,
                }
                for record in sorted(self.records, key=lambda item: item.external_id)
            ],
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        if len(encoded) > 64 * 1024 * 1024:
            raise ValueError("invalid_collected_policy_batch")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class CollectionImportResult:
    new: int
    changed: int
    unchanged: int
    missing: int
    replayed: bool = False


class CollectedPolicyStore(Protocol):
    def save(self, batch: CollectedPolicyBatch) -> CollectionImportResult:
        """Atomically store a complete generation and its own DB change events."""
        ...
