"""Bounded GOV24 public-service collection and inert analysis projection.

The collector treats ``matchCount`` as the count matching the explicit service-ID
filter. ``totalCount`` remains the source-wide count and is never used as a page or
record count. A generation is saved only after every selected request and record has
passed validation; failures never replace the last successful version.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime

from benefitfinder.application.collection import (
    CollectedRecord,
    RawSnapshot,
    RawSnapshotStore,
    RecordVersionStore,
    SourceFetcher,
    SourceRequest,
    SourceResponse,
)
from benefitfinder.ingestion.collect import CollectionError, CollectionResult
from benefitfinder.ingestion.versioning import diff_records, record_sha256, sha256_hex

SOURCE_NAME = "gov24"
MAX_SELECTED_IDS = 100
MIN_YOUTH_AGE = 19
MAX_YOUTH_AGE = 39
MIN_REASONABLE_AGE = 0
MAX_REASONABLE_AGE = 120
ID_FIELD = "서비스ID"
NAME_FIELD = "서비스명"
CATEGORY_FIELD = "서비스분야"
VIEW_COUNT_FIELD = "조회수"
AGE_START_FIELD = "JA0110"
AGE_END_FIELD = "JA0111"
ID_PARAM = "cond[서비스ID::EQ]"

_ENDPOINTS = {
    "serviceList": "https://api.odcloud.kr/api/gov24/v3/serviceList",
    "serviceDetail": "https://api.odcloud.kr/api/gov24/v3/serviceDetail",
    "supportConditions": "https://api.odcloud.kr/api/gov24/v3/supportConditions",
}
_RAW_SOURCE_NAMES = {
    "serviceList": "gov24-serviceList",
    "serviceDetail": "gov24-serviceDetail",
    "supportConditions": "gov24-supportConditions",
}
_ENVELOPE_KEYS = {
    "page",
    "perPage",
    "totalCount",
    "currentCount",
    "matchCount",
    "data",
}
_CATEGORIES = frozenset({"employment", "housing"})
_COMPOSITE_KEYS = frozenset({"serviceList", "serviceDetail", "supportConditions"})


class Gov24ParseError(ValueError):
    """A fixed, safe response validation failure."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


def _require_external_id(external_id: object) -> str:
    if type(external_id) is not str or not external_id or len(external_id) > 200:
        raise ValueError("external_id must be a non-empty string of at most 200 characters")
    if external_id != external_id.strip():
        raise ValueError("external_id must not contain surrounding whitespace")
    return external_id


def gov24_request(endpoint: str, external_id: str) -> SourceRequest:
    """Return the fixed one-record request for a GOV24 endpoint.

    Credentials are intentionally absent. ``Gov24HttpFetcher`` adds the official
    Authorization header only when sending the request.
    """
    if endpoint not in _ENDPOINTS:
        raise ValueError("unsupported GOV24 endpoint")
    service_id = _require_external_id(external_id)
    return SourceRequest(
        url=_ENDPOINTS[endpoint],
        params={
            ID_PARAM: service_id,
            "page": "1",
            "perPage": "1",
            "returnType": "JSON",
        },
    )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise Gov24ParseError("duplicate_json_key", "duplicate JSON object key")
        result[key] = value
    return result


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise Gov24ParseError("nonfinite_number", "non-finite number is not allowed")
    return parsed


def _reject_constant(value: str) -> object:
    raise Gov24ParseError("nonfinite_number", "non-finite number is not allowed")


def _integer(document: Mapping[str, object], key: str) -> int:
    value = document[key]
    if type(value) is not int:
        raise Gov24ParseError("invalid_response", f"{key} must be an integer")
    return value


def parse_gov24_response(
    body: bytes,
    external_id: str,
    *,
    allow_empty: bool = False,
) -> dict[str, object] | None:
    """Parse one filtered GOV24 response and return its sole row or ``None``.

    Empty responses are accepted only with ``allow_empty=True``. The collector uses
    that setting for the list probe; absent details and support conditions fail.
    """
    service_id = _require_external_id(external_id)
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Gov24ParseError("invalid_json", "response is not UTF-8 JSON") from exc
    try:
        document = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_float=_finite_float,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise Gov24ParseError("invalid_json", "response is not valid JSON") from exc
    if type(document) is not dict or set(document) != _ENVELOPE_KEYS:
        raise Gov24ParseError("invalid_response", "unexpected response envelope")

    page = _integer(document, "page")
    per_page = _integer(document, "perPage")
    total_count = _integer(document, "totalCount")
    current_count = _integer(document, "currentCount")
    match_count = _integer(document, "matchCount")
    data = document["data"]
    if type(data) is not list:
        raise Gov24ParseError("invalid_response", "data must be an array")
    if page != 1 or per_page != 1:
        raise Gov24ParseError("invalid_response", "page and perPage must be 1")
    if total_count < 0 or match_count not in {0, 1}:
        raise Gov24ParseError("invalid_count", "count is outside the bounded range")
    if current_count != len(data) or match_count != len(data):
        raise Gov24ParseError("count_mismatch", "response counts do not match data")
    if match_count > total_count:
        raise Gov24ParseError("count_mismatch", "matchCount exceeds totalCount")
    if not data:
        if allow_empty:
            return None
        raise Gov24ParseError("empty_response", "detail or support response is empty")

    row = data[0]
    if type(row) is not dict:
        raise Gov24ParseError("invalid_response", "data row must be an object")
    if type(row.get(ID_FIELD)) is not str or row[ID_FIELD] != service_id:
        raise Gov24ParseError("service_id_mismatch", "service ID does not match request")
    return row


def _validate_category_values(
    category_values: Mapping[str, tuple[str, ...]],
) -> dict[str, frozenset[str]]:
    if not isinstance(category_values, Mapping) or not category_values:
        raise ValueError("category_values must be a non-empty mapping")
    normalized: dict[str, frozenset[str]] = {}
    seen: set[str] = set()
    for category, values in category_values.items():
        if type(category) is not str or category not in _CATEGORIES:
            raise ValueError("only exact employment and housing categories are supported")
        if type(values) is not tuple or not values:
            raise ValueError("each category must have a non-empty tuple of source values")
        if any(type(value) is not str or not value or value != value.strip() for value in values):
            raise ValueError("source category values must be non-empty exact strings")
        if len(set(values)) != len(values):
            raise ValueError("source category values must not repeat")
        if seen.intersection(values):
            raise ValueError("a source category value cannot map to multiple categories")
        seen.update(values)
        normalized[category] = frozenset(values)
    return normalized


def _validate_selected_ids(external_ids: tuple[str, ...]) -> tuple[str, ...]:
    if type(external_ids) is not tuple or not 1 <= len(external_ids) <= MAX_SELECTED_IDS:
        raise ValueError("external_ids must be a tuple containing 1 to 100 IDs")
    if len(set(external_ids)) != len(external_ids):
        raise ValueError("external_ids must not contain duplicates")
    return tuple(_require_external_id(external_id) for external_id in external_ids)


def _category_for(row: Mapping[str, object], categories: Mapping[str, frozenset[str]]) -> str:
    value = row.get(CATEGORY_FIELD)
    if type(value) is not str or not value:
        raise Gov24ParseError("invalid_category", "service category is missing")
    matches = [category for category, values in categories.items() if value in values]
    if len(matches) != 1:
        raise Gov24ParseError("category_out_of_scope", "service category is not selected")
    return matches[0]


def _service_name(row: Mapping[str, object]) -> str:
    value = row.get(NAME_FIELD)
    if type(value) is not str or not value.strip():
        raise Gov24ParseError("invalid_service_name", "service name is missing")
    return value


def _overlaps_youth_age(row: Mapping[str, object]) -> bool:
    start = row.get(AGE_START_FIELD)
    end = row.get(AGE_END_FIELD)
    if type(start) is not int or type(end) is not int:
        raise Gov24ParseError("invalid_age", "both age bounds must be integers")
    if not (
        MIN_REASONABLE_AGE <= start <= end <= MAX_REASONABLE_AGE
    ):
        raise Gov24ParseError("invalid_age", "age bounds are not reasonable")
    return end >= MIN_YOUTH_AGE and start <= MAX_YOUTH_AGE


def _now(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value


@dataclass(frozen=True)
class _FetchedRow:
    row: dict[str, object] | None
    snapshot_ref: str
    content_sha256: str
    fetched_at: datetime


def _snapshot(
    response: SourceResponse,
    request: SourceRequest,
    source_name: str,
    fetched_at: datetime,
) -> RawSnapshot:
    return RawSnapshot(
        source_name=source_name,
        request=request,
        fetched_at=fetched_at,
        status_code=response.status_code,
        content_type=response.content_type,
        content_sha256=sha256_hex(response.body),
        body=response.body,
    )


def _manifest_body(
    external_id: str,
    responses: Mapping[str, Mapping[str, str]],
) -> bytes:
    manifest = {
        "format_version": 1,
        "external_id": external_id,
        "responses": {
            "serviceList": dict(responses["serviceList"]),
            "serviceDetail": dict(responses["serviceDetail"]),
            "supportConditions": dict(responses["supportConditions"]),
        },
    }
    return json.dumps(
        manifest,
        ensure_ascii=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def _collect_one(
    external_id: str,
    categories: Mapping[str, frozenset[str]],
    fetcher: SourceFetcher,
    snapshot_store: RawSnapshotStore,
    clock: Callable[[], datetime],
    error_page: int,
) -> tuple[CollectedRecord | None, list[CollectionError]]:
    errors: list[CollectionError] = []
    fetched: dict[str, _FetchedRow] = {}
    for endpoint in ("serviceList", "serviceDetail", "supportConditions"):
        request = gov24_request(endpoint, external_id)
        try:
            response = fetcher.fetch(request)
        except Exception as exc:  # Never expose adapter exception text or URLs.
            errors.append(
                CollectionError(error_page, "fetch_failed", type(exc).__name__, external_id=external_id)
            )
            return None, errors
        fetched_at = _now(clock)
        raw = _snapshot(response, request, _RAW_SOURCE_NAMES[endpoint], fetched_at)
        try:
            snapshot_ref = snapshot_store.save(raw)
        except Exception as exc:  # Storage messages may contain local paths.
            errors.append(
                CollectionError(
                    error_page,
                    "snapshot_failed",
                    type(exc).__name__,
                    external_id=external_id,
                )
            )
            return None, errors
        if not 200 <= response.status_code < 300:
            errors.append(
                CollectionError(
                    error_page,
                    "http_status",
                    str(response.status_code),
                    snapshot_ref,
                    external_id,
                )
            )
            return None, errors
        try:
            row = parse_gov24_response(
                response.body,
                external_id,
                allow_empty=endpoint == "serviceList",
            )
        except Gov24ParseError as exc:
            errors.append(
                CollectionError(
                    error_page,
                    exc.code,
                    str(exc),
                    snapshot_ref,
                    external_id,
                )
            )
            return None, errors
        if row is None:
            if endpoint != "serviceList":
                raise AssertionError("non-list empty response passed validation")
            return None, errors
        fetched[endpoint] = _FetchedRow(row, snapshot_ref, raw.content_sha256, fetched_at)

    list_row = fetched["serviceList"].row
    detail_row = fetched["serviceDetail"].row
    conditions_row = fetched["supportConditions"].row
    assert list_row is not None and detail_row is not None and conditions_row is not None
    try:
        _category_for(list_row, categories)
        list_name = _service_name(list_row)
        if _service_name(detail_row) != list_name:
            raise Gov24ParseError("service_name_mismatch", "service names are inconsistent")
        in_scope = _overlaps_youth_age(conditions_row)
    except Gov24ParseError as exc:
        snapshot_ref = fetched["supportConditions"].snapshot_ref
        errors.append(
            CollectionError(error_page, exc.code, str(exc), snapshot_ref, external_id)
        )
        return None, errors
    if not in_scope:
        # All three responses were valid and saved: this is a confirmed scope removal.
        return None, errors

    manifest_fetched_at = max(item.fetched_at for item in fetched.values())
    manifest_responses = {
        endpoint: {
            "snapshot_ref": item.snapshot_ref,
            "content_sha256": item.content_sha256,
        }
        for endpoint, item in fetched.items()
    }
    manifest_request = gov24_request("serviceList", external_id)
    manifest_body = _manifest_body(external_id, manifest_responses)
    manifest = RawSnapshot(
        source_name=SOURCE_NAME,
        request=manifest_request,
        fetched_at=manifest_fetched_at,
        status_code=200,
        content_type="application/json",
        content_sha256=sha256_hex(manifest_body),
        body=manifest_body,
    )
    try:
        manifest_ref = snapshot_store.save(manifest)
    except Exception as exc:  # Storage messages may contain local paths.
        errors.append(
            CollectionError(
                error_page,
                "snapshot_failed",
                type(exc).__name__,
                external_id=external_id,
            )
        )
        return None, errors
    fields = {
        "serviceList": list_row,
        "serviceDetail": detail_row,
        "supportConditions": conditions_row,
    }
    record = CollectedRecord(
        source_name=SOURCE_NAME,
        external_id=external_id,
        record_sha256=record_sha256(gov24_hashed_fields(fields)),
        snapshot_ref=manifest_ref,
        fetched_at=manifest_fetched_at,
        fields=fields,
    )
    return record, errors


def _require_composite(fields: Mapping[str, object]) -> dict[str, dict[str, object]]:
    if not isinstance(fields, Mapping) or set(fields) != _COMPOSITE_KEYS:
        raise ValueError("fields must be the exact GOV24 composite")
    result: dict[str, dict[str, object]] = {}
    for key in _COMPOSITE_KEYS:
        value = fields[key]
        if type(value) is not dict:
            raise ValueError("each composite value must be an object")
        result[key] = value
    return result


def gov24_hashed_fields(fields: Mapping[str, object]) -> dict[str, object]:
    """Return material fields, excluding only the list view counter."""
    rows = _require_composite(fields)
    return {
        "serviceList": {
            key: value for key, value in rows["serviceList"].items() if key != VIEW_COUNT_FIELD
        },
        "serviceDetail": dict(rows["serviceDetail"]),
        "supportConditions": dict(rows["supportConditions"]),
    }


def analysis_fields(fields: Mapping[str, object]) -> dict[str, object]:
    """Project only inert, allowlisted detail fields under safe ASCII aliases."""
    rows = _require_composite(fields)
    mappings = (
        ("serviceId", "serviceList", ID_FIELD),
        ("serviceName", "serviceList", NAME_FIELD),
        ("serviceCategory", "serviceList", CATEGORY_FIELD),
        ("ageStart", "supportConditions", AGE_START_FIELD),
        ("ageEnd", "supportConditions", AGE_END_FIELD),
        ("detailPurpose", "serviceDetail", "서비스목적"),
        ("detailTarget", "serviceDetail", "지원대상"),
        ("detailCriteria", "serviceDetail", "선정기준"),
        ("detailSupport", "serviceDetail", "지원내용"),
        ("detailMethod", "serviceDetail", "신청방법"),
        ("detailDeadline", "serviceDetail", "신청기한"),
        ("detailDocuments", "serviceDetail", "구비서류"),
        ("detailLaw", "serviceDetail", "법령"),
        ("detailLocalLaw", "serviceDetail", "자치법규"),
        ("detailAdministrativeRule", "serviceDetail", "행정규칙"),
    )
    projected: dict[str, object] = {}
    for alias, part, source_key in mappings:
        if source_key in rows[part]:
            projected[alias] = rows[part][source_key]
    return projected


def collect_gov24(
    external_ids: tuple[str, ...],
    category_values: Mapping[str, tuple[str, ...]],
    fetcher: SourceFetcher,
    snapshot_store: RawSnapshotStore,
    version_store: RecordVersionStore,
    clock: Callable[[], datetime],
) -> CollectionResult:
    """Collect an explicit bounded GOV24 set and save one complete generation."""
    selected_ids = _validate_selected_ids(external_ids)
    categories = _validate_category_values(category_values)
    started_at = _now(clock)
    records: list[CollectedRecord] = []
    errors: list[CollectionError] = []

    for index, external_id in enumerate(selected_ids, start=1):
        record, item_errors = _collect_one(
            external_id,
            categories,
            fetcher,
            snapshot_store,
            clock,
            index,
        )
        errors.extend(item_errors)
        if record is not None:
            records.append(record)

    if errors:
        return CollectionResult(
            source_name=SOURCE_NAME,
            started_at=started_at,
            records=tuple(records),
            changes=(),
            errors=tuple(errors),
            complete=False,
        )

    selected = set(selected_ids)
    previous = {
        external_id: digest
        for external_id, digest in version_store.latest_hashes(SOURCE_NAME).items()
        if external_id in selected
    }
    changes = tuple(diff_records(SOURCE_NAME, previous, records, complete=True))
    version_store.save_versions(records, changes, checked_at=_now(clock))
    return CollectionResult(
        source_name=SOURCE_NAME,
        started_at=started_at,
        records=tuple(records),
        changes=changes,
        errors=(),
        complete=True,
    )
