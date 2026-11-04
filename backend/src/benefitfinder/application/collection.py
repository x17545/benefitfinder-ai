"""Interfaces and records for collecting official policy sources.

ingestion builds collection flows on these types, and infrastructure implements the
network and storage protocols. Neither imports the other; the assembly point wires
concrete adapters in. Records keep raw source values as received and never interpret
eligibility, amounts, or duplicate-benefit rules.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol


@dataclass(frozen=True)
class SourceRequest:
    """One request to an official source.

    ``params`` never holds API keys or other secrets. The fetcher adapter adds
    credentials itself, so stored requests can be shared and logged safely.
    """

    url: str
    params: Mapping[str, str]


@dataclass(frozen=True)
class SourceResponse:
    """Raw response bytes from an official source, before any parsing."""

    status_code: int
    body: bytes
    content_type: str | None


class SourceFetcher(Protocol):
    """Network adapter for official sources (implemented in infrastructure)."""

    def fetch(self, request: SourceRequest) -> SourceResponse:
        """Fetch one request. Raise on transport failure; return non-2xx as-is."""
        ...


@dataclass(frozen=True)
class RawSnapshot:
    """An unmodified source response with when and how it was collected."""

    source_name: str
    request: SourceRequest
    fetched_at: datetime
    status_code: int
    content_type: str | None
    content_sha256: str
    body: bytes


class RawSnapshotStore(Protocol):
    """Keeps raw snapshots outside Git (implemented in infrastructure)."""

    def save(self, snapshot: RawSnapshot) -> str:
        """Store the snapshot and return a stable reference to it."""
        ...


@dataclass(frozen=True)
class CollectedRecord:
    """One source record as received, identified by the source's own ID."""

    source_name: str
    external_id: str
    record_sha256: str
    snapshot_ref: str
    fetched_at: datetime
    fields: Mapping[str, object]


class ChangeKind(StrEnum):
    """How a record compares with the last successfully stored version."""

    NEW = "new"
    CHANGED = "changed"
    UNCHANGED = "unchanged"
    MISSING = "missing"


@dataclass(frozen=True)
class RecordChange:
    """A version change for one source record."""

    source_name: str
    external_id: str
    kind: ChangeKind
    previous_sha256: str | None
    current_sha256: str | None
    snapshot_ref: str | None


class RecordVersionStore(Protocol):
    """Latest record versions per source (implemented in infrastructure)."""

    def latest_hashes(self, source_name: str) -> Mapping[str, str]:
        """Return ``{external_id: record_sha256}`` of the last stored versions."""
        ...

    def save_versions(
        self,
        records: Sequence[CollectedRecord],
        changes: Sequence[RecordChange],
        *,
        checked_at: datetime,
    ) -> None:
        """Store records and events together, recovering unfinished writes before reads.

        ``checked_at`` is the timezone-aware collection time, including empty runs.
        """
        ...
