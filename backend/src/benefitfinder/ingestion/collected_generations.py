"""Validate a complete local youthcenter generation before offline DB import."""

from collections.abc import Callable
from datetime import datetime
import hashlib
import json
from pathlib import PurePosixPath
import re

from benefitfinder.application.collection import CollectedRecord
from benefitfinder.application.collected_policies import CollectedPolicyBatch
from benefitfinder.ingestion.versioning import record_sha256
from benefitfinder.ingestion.youthcenter import (
    SOURCE_NAME,
    YOUTHCENTER_POLICY_LIST,
    external_id_of,
    hashed_fields,
    page_request,
    parse_page,
    stored_fields,
)


class CollectedGenerationError(ValueError):
    """Invalid/missing source files, without leaking their contents or paths."""


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError


def _json(raw: bytes) -> dict[str, object]:
    value = json.loads(raw, object_pairs_hook=_object, parse_constant=_reject_constant)
    if not isinstance(value, dict):
        raise ValueError
    return value


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError
    return parsed


def load_youthcenter_generation(
    read_file: Callable[[str, int], bytes],
) -> CollectedPolicyBatch:
    """Read only the selected complete generation through a bounded file adapter.

    Source fields must match the preserved response and its hash. These checks
    establish local integrity, not human approval or authenticity of edited files.
    """
    try:
        return _load(read_file)
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        OverflowError,
    ):
        raise CollectedGenerationError("invalid_collected_generation") from None


def _load(read_file: Callable[[str, int], bytes]) -> CollectedPolicyBatch:
    root = "processed/f08-youthcenter"
    status = _json(read_file(f"{root}/status.json", 65_536))
    if type(status.get("format_version")) is not int or status["format_version"] != 1:
        raise ValueError
    success = status["last_success"]
    if not isinstance(success, dict) or set(success) != {"run_id", "completed_at", "record_count"}:
        raise ValueError
    run_id = success["run_id"]
    count = success["record_count"]
    if (
        not isinstance(run_id, str)
        or re.fullmatch(r"[0-9a-f]{32}", run_id) is None
        or type(count) is not int
        or not 0 <= count <= 50_000
    ):
        raise ValueError
    completed_at = _timestamp(success["completed_at"])
    entries = _json(read_file(f"{root}/runs/{run_id}/{SOURCE_NAME}/records.json", 64 * 1024 * 1024))
    records = []
    pages: dict[str, tuple[datetime, dict[str, list[dict[str, object]]]]] = {}
    config = YOUTHCENTER_POLICY_LIST
    for external_id, entry in entries.items():
        if not isinstance(entry, dict) or set(entry) != {
            "record_sha256",
            "snapshot_ref",
            "last_checked_at",
            "missing_detected_at",
            "fields",
        }:
            raise ValueError
        if entry["missing_detected_at"] is not None:
            if _timestamp(entry["missing_detected_at"]) > completed_at:
                raise ValueError
            continue
        fields = entry["fields"]
        fetched_at = _timestamp(entry["last_checked_at"])
        if (
            not isinstance(fields, dict)
            or external_id_of(config, fields) != external_id
            or stored_fields(config, fields) != fields
            or record_sha256(hashed_fields(config, fields)) != entry["record_sha256"]
            or fetched_at > completed_at
        ):
            raise ValueError
        reference = entry["snapshot_ref"]
        if not isinstance(reference, str):
            raise ValueError
        relative = PurePosixPath(reference)
        if (
            relative.parts[:2] != (SOURCE_NAME, "snapshots")
            or len(relative.parts) != 3
            or relative.suffix not in {".json", ".body"}
            or ".." in relative.parts
        ):
            raise ValueError
        if reference not in pages:
            source_path = f"raw/f08-youthcenter/{relative.as_posix()}"
            metadata_path = f"raw/f08-youthcenter/{relative.with_suffix('.meta.json').as_posix()}"
            body = read_file(source_path, 16 * 1024 * 1024)
            metadata = _json(read_file(metadata_path, 65_536))
            params = metadata["request_params"]
            if not isinstance(params, dict):
                raise ValueError
            page = params[config.page_param]
            if not isinstance(page, str) or not page.isascii() or not page.isdigit():
                raise ValueError
            page_number = int(page)
            digest = hashlib.sha256(body).hexdigest()
            if (
                metadata["source_name"] != SOURCE_NAME
                or metadata["request_url"] != config.endpoint
                or not 1 <= page_number <= config.max_pages
                or params != dict(page_request(config, page_number).params)
                or type(metadata["status_code"]) is not int
                or not 200 <= metadata["status_code"] < 300
                or metadata["content_sha256"] != digest
                or metadata["body_file"] != relative.name
                or not relative.stem.endswith("-" + digest[:12])
            ):
                raise ValueError
            _json(body)  # Reject duplicate keys and non-finite numbers before source parsing.
            page_records, _ = parse_page(config, body)
            by_id: dict[str, list[dict[str, object]]] = {}
            for item in page_records:
                source_id = external_id_of(config, item)
                if source_id is not None:
                    by_id.setdefault(source_id, []).append(stored_fields(config, item))
            pages[reference] = (_timestamp(metadata["fetched_at"]), by_id)
        source_at, by_id = pages[reference]
        if source_at != fetched_at or fields not in by_id.get(external_id, ()):
            raise ValueError
        records.append(
            CollectedRecord(
                SOURCE_NAME, external_id, entry["record_sha256"], reference, fetched_at, fields
            )
        )
    if len(records) != count:
        raise ValueError
    return CollectedPolicyBatch(SOURCE_NAME, run_id, completed_at, tuple(records))
