"""Read-only pending policy recheck records from PostgreSQL."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row


LIST_POLICY_RECHECKS_SQL = """
SELECT q.task_id, q.policy_id, q.document_version, q.reason,
       q.source, q.external_id, q.run_id,
       q.approved_material_sha256, q.current_material_sha256,
       q.detected_at, q.approved_sha256, q.reviewed_by, q.reviewed_at,
       q.invalidated_document_versions,
       p.material_sha256 AS latest_material_sha256,
       p.missing_detected_at AS latest_missing_detected_at,
       p.last_run_id AS latest_run_id,
       (SELECT newer.document_version
        FROM public.policy_catalog_releases original
        JOIN public.policy_catalog_releases newer ON newer.policy_id = original.policy_id
          AND newer.published_at > original.published_at
        WHERE original.policy_id = q.policy_id AND original.document_version = q.document_version
        ORDER BY newer.published_at DESC, newer.document_version LIMIT 1) AS superseded_by_version
FROM public.policy_recheck_queue AS q
LEFT JOIN public.collected_policies AS p
  ON p.source = q.source AND p.external_id = q.external_id
WHERE q.task_id > %s
ORDER BY q.task_id ASC
LIMIT %s
"""

_INVALID_DATABASE_URL = "invalid_policy_recheck_database_url"
_INVALID_AFTER_ID = "invalid_policy_recheck_after_id"
_INVALID_LIMIT = "invalid_policy_recheck_limit"
_LOOKUP_FAILED = "policy_recheck_lookup_failed"
_REASONS = frozenset({"source_changed", "source_missing", "source_unlinked"})
_MAX_BIGINT = 2**63 - 1


def _validated_database_url(database_url: object) -> str:
    if type(database_url) is not str or not database_url.strip():
        raise ValueError(_INVALID_DATABASE_URL)
    try:
        config = conninfo_to_dict(database_url)
    except (psycopg.Error, ValueError):
        raise ValueError(_INVALID_DATABASE_URL) from None
    if not config.get("dbname"):
        raise ValueError(_INVALID_DATABASE_URL)
    return database_url


def _row_value(row: Mapping[str, object], key: str) -> object:
    try:
        return row[key]
    except KeyError:
        raise ValueError from None


def _required_text(row: Mapping[str, object], key: str) -> str:
    value = _row_value(row, key)
    if type(value) is not str or not value:
        raise ValueError
    return value


def _optional_text(row: Mapping[str, object], key: str) -> str | None:
    value = _row_value(row, key)
    if value is None:
        return None
    if type(value) is not str or not value:
        raise ValueError
    return value


def _iso_timestamp(
    row: Mapping[str, object], key: str, *, optional: bool = False
) -> str | None:
    value = _row_value(row, key)
    if value is None and optional:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError
    return value.astimezone(timezone.utc).isoformat()


def _document_versions(row: Mapping[str, object]) -> list[str]:
    value = _row_value(row, "invalidated_document_versions")
    if type(value) is not list or any(type(item) is not str or not item for item in value):
        raise ValueError
    return list(value)


def _item_from_row(row: Mapping[str, object]) -> dict[str, object]:
    task_id = _row_value(row, "task_id")
    if type(task_id) is not int or task_id < 1:
        raise ValueError
    reason = _required_text(row, "reason")
    if reason not in _REASONS:
        raise ValueError

    source = _optional_text(row, "source")
    superseded_by = _optional_text(row, "superseded_by_version")
    external_id = _optional_text(row, "external_id")
    run_id = _optional_text(row, "run_id")
    if reason != "source_unlinked" and any(
        value is None for value in (source, external_id, run_id)
    ):
        raise ValueError

    return {
        "task_id": task_id,
        "policy_id": _required_text(row, "policy_id"),
        "document_version": _required_text(row, "document_version"),
        "reason": reason,
        "source": source,
        "external_id": external_id,
        "run_id": run_id,
        "approved_material_sha256": _optional_text(row, "approved_material_sha256"),
        "current_material_sha256": _optional_text(row, "current_material_sha256"),
        "detected_at": _iso_timestamp(row, "detected_at"),
        "approved_sha256": _required_text(row, "approved_sha256"),
        "reviewed_by": _optional_text(row, "reviewed_by"),
        "reviewed_at": _iso_timestamp(row, "reviewed_at", optional=True),
        "invalidated_document_versions": _document_versions(row),
        "latest_material_sha256": _optional_text(row, "latest_material_sha256"),
        "latest_missing_detected_at": _iso_timestamp(
            row, "latest_missing_detected_at", optional=True
        ),
        "latest_run_id": _optional_text(row, "latest_run_id"),
        "superseded_by_version": superseded_by,
        "recheck_status": "pending" if superseded_by is None else "superseded",
    }


def list_policy_rechecks(
    database_url: str, *, after_id: int = 0, limit: int = 100
) -> dict[str, object]:
    """Return one ascending, read-only page of immutable recheck audit rows."""
    validated_url = _validated_database_url(database_url)
    if type(after_id) is not int or not 0 <= after_id <= _MAX_BIGINT:
        raise ValueError(_INVALID_AFTER_ID)
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError(_INVALID_LIMIT)

    try:
        with psycopg.connect(
            validated_url,
            connect_timeout=2,
            options="-c default_transaction_read_only=on -c statement_timeout=2000 "
            "-c lock_timeout=500 -c timezone=UTC",
            application_name="benefitfinder-policy-rechecks",
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(LIST_POLICY_RECHECKS_SQL, (after_id, limit + 1))
                rows = cursor.fetchall()

        if type(rows) is not list or len(rows) > limit + 1:
            raise ValueError
        items: list[dict[str, object]] = []
        previous_id = after_id
        for row in rows:
            if not isinstance(row, Mapping):
                raise ValueError
            item = _item_from_row(row)
            task_id = item["task_id"]
            if type(task_id) is not int or task_id <= previous_id:
                raise ValueError
            previous_id = task_id
            items.append(item)
    except (psycopg.Error, OSError, TimeoutError, KeyError, TypeError, ValueError):
        raise RuntimeError(_LOOKUP_FAILED) from None

    has_more = len(items) > limit
    page = items[:limit]
    next_after_id = page[-1]["task_id"] if has_more else None
    return {
        "format_version": 1,
        "kind": "policy_recheck_page",
        "items": page,
        "next_after_id": next_after_id,
    }
