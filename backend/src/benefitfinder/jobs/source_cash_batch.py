"""Deterministic source-cash batch orchestration with injected storage and publishing.

This module deliberately has no infrastructure imports. The CLI assembles the database
reader, region catalog, review metadata and publication callback. A checkpoint is evidence
for restart diagnostics only; every run re-reads the current release before publication.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from benefitfinder.application.decider import Compilation
from benefitfinder.application.policy_analysis import PolicyAnalysisInput


SOURCE_CASH_SOURCE_VERSION = "source-facts-v1"
SOURCE_CASH_REVIEW_METADATA = "deterministic-code:no-model-review"
SOURCE_CASH_WRITER_AGENT = "source-cash-compiler"
SOURCE_CASH_REVIEWER_AGENT = "source-facts-deterministic-audit"


@dataclass(frozen=True, slots=True)
class PreparedSourceCash:
    """An opaque publication record plus its existing AIReview storage metadata."""

    policy_id: str
    document_version: str
    record_sha256: str
    source_version: str
    record: object
    review: object
    source_text: str


@dataclass
class _Item:
    value: PolicyAnalysisInput
    aliases: tuple[str, ...]
    external_ids: tuple[str, ...]
    entry: dict
    checkpoint: Path
    prepared: PreparedSourceCash | None = None


_SAFE_ERROR = re.compile(
    r"(?:source_cash_batch_|source_cash_|source_import_|ai_catalog_|ai_review_|catalog_)"
    r"[a-z0-9_]{1,120}"
)


def _fingerprint(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _safe_error(error: Exception, fallback: str) -> dict:
    code = str(error)
    if _SAFE_ERROR.fullmatch(code) is None:
        primary = getattr(getattr(error, "diag", None), "message_primary", "")
        code = primary if isinstance(primary, str) and _SAFE_ERROR.fullmatch(primary) else fallback
    result = {"code": code, "exception_type": type(error).__name__}
    state = getattr(error, "sqlstate", None)
    if isinstance(state, str) and re.fullmatch(r"[A-Z0-9]{5}", state):
        result["sqlstate"] = state
    return result


def _aliases(value: PolicyAnalysisInput) -> tuple[str, ...]:
    names = {value.policy_id}
    for binding in value.collection_sources:
        if binding.source == "youthcenter":
            names.update(("youth:" + binding.external_id, "youth:youthcenter:" + binding.external_id))
    return tuple(sorted(names))


def _external_ids(value: PolicyAnalysisInput) -> tuple[str, ...]:
    return tuple(sorted(
        binding.external_id for binding in value.collection_sources
        if binding.source == "youthcenter"
    ))


def source_cash_review_timestamp(*, checked_at: datetime, now: datetime | None = None) -> datetime:
    """Use actual approval time and reject source material checked in the future."""
    current = now or datetime.now(timezone.utc)
    if checked_at.utcoffset() is None or current.utcoffset() is None:
        raise ValueError("source_cash_batch_review_time_requires_timezone")
    if checked_at > current:
        raise ValueError("source_cash_batch_source_time_in_future")
    return current


def restore_checked_at_only(candidate: object, approved: object) -> dict | None:
    """Copy approved ``checked_at`` values; any other payload difference returns ``None``."""

    def walk(left: object, right: object) -> tuple[bool, object]:
        if isinstance(left, dict) and isinstance(right, dict):
            if set(left) != set(right):
                return False, None
            restored = {}
            for key, value in left.items():
                expected = right[key]
                if key == "checked_at":
                    if type(value) is not str or type(expected) is not str:
                        return False, None
                    restored[key] = expected
                    continue
                valid, updated = walk(value, expected)
                if not valid:
                    return False, None
                restored[key] = updated
            return True, restored
        if isinstance(left, list) and isinstance(right, list):
            if len(left) != len(right):
                return False, None
            restored = []
            for value, expected in zip(left, right, strict=True):
                valid, updated = walk(value, expected)
                if not valid:
                    return False, None
                restored.append(updated)
            return True, restored
        return (True, left) if left == right else (False, None)

    if not isinstance(candidate, dict) or not isinstance(approved, dict):
        return None
    valid, result = walk(deepcopy(candidate), deepcopy(approved))
    return result if valid and isinstance(result, dict) else None


def _identity_decision(
    prepared: PreparedSourceCash,
    aliases: tuple[str, ...],
    external_ids: tuple[str, ...],
    identities: Sequence[Mapping[str, object]],
) -> tuple[str, str | None, str | None]:
    """Return action, hold reason, and expected previous digest for CAS replacement."""
    matches = [
        row for row in identities
        if row.get("policy_id") in aliases
        or (row.get("source") == "youthcenter" and row.get("external_id") in external_ids)
    ]
    if not matches:
        return "new", None, None

    if any(row.get("policy_id") != prepared.policy_id for row in matches):
        return "held", "source_cash_batch_alias_conflict", None

    current = [row for row in matches if row.get("publication_status") == "published"]
    if not current:
        return "held", "source_cash_batch_retired_release_protected", None
    if len(current) != 1:
        return "held", "source_cash_batch_current_release_conflict", None

    row = current[0]
    review_type = row.get("review_type")
    review_status = row.get("review_status")
    version = row.get("document_version")
    digest = row.get("record_sha256")
    if not all(type(value) is str for value in (version, digest)):
        return "held", "source_cash_batch_release_identity_invalid", None

    if review_type == "human":
        return "held", "source_cash_batch_human_release_protected", None
    if review_type == "ai":
        if review_status != "approved":
            return "held", "source_cash_batch_ai_release_protected", None
        if version == prepared.document_version:
            if digest == prepared.record_sha256:
                return "replayed", None, None
            return "held", "source_cash_batch_version_hash_conflict", None
        if version.endswith(":" + prepared.source_version):
            return "replace_ai", None, digest
        return "held", "source_cash_batch_ai_release_protected", None
    if review_type == "unreviewed":
        return "replace_unreviewed", None, digest
    return "held", "source_cash_batch_existing_release_protected", None


def run_source_cash_batch(
    values: Sequence[PolicyAnalysisInput],
    *,
    output: Path,
    as_of: date,
    compile_source: Callable[[PolicyAnalysisInput], Compilation],
    prepare: Callable[[Compilation, str, date], PreparedSourceCash],
    load_existing: Callable[
        [tuple[str, ...], tuple[str, ...]], Sequence[Mapping[str, object]]
    ],
    load_current_release: Callable[[str, str], Mapping[str, object] | None] | None = None,
    timestamp_replay: Callable[[PreparedSourceCash, Mapping[str, object]], bool] | None = None,
    publish: Callable[
        [list[PreparedSourceCash], Mapping[str, str], bool, bool], Mapping[str, int]
    ] | None,
    save: Callable[[Path, object], None],
    load: Callable[[Path], dict | None],
    selection: Mapping[str, object] | None = None,
    batch_size: int = 10,
) -> dict:
    """Compile once, checkpoint each item, then optionally publish bounded transactions."""
    if type(batch_size) is not int or not 1 <= batch_size <= 50:
        raise ValueError("source_cash_batch_invalid_batch_size")

    unique: dict[str, PolicyAnalysisInput] = {}
    duplicates = 0
    for value in values:
        prior = unique.get(value.policy_id)
        if prior is not None:
            if prior.fingerprint() != value.fingerprint():
                raise ValueError("source_cash_batch_conflicting_inputs")
            duplicates += 1
        else:
            unique[value.policy_id] = value

    items: list[_Item] = []

    def checkpoint(item: _Item) -> None:
        saved_entry = {
            key: value for key, value in item.entry.items()
            if key not in {"checkpoint_payload", "checkpoint_normalized"}
        }
        save(item.checkpoint, {
            "format_version": 1,
            **saved_entry,
            "payload": item.entry.get("checkpoint_payload"),
            "normalized": item.entry.get("checkpoint_normalized", {}),
            "source_version": SOURCE_CASH_SOURCE_VERSION,
            "review_metadata": SOURCE_CASH_REVIEW_METADATA,
            "model_calls": 0,
            "ai_review_calls": 0,
            "ai_verification": "not_performed",
        })

    for value in unique.values():
        input_sha256 = value.fingerprint()
        key = _fingerprint({"policy_id": value.policy_id, "input_sha256": input_sha256})
        path = output / "items" / key / "checkpoint.json"
        entry = {
            "policy_id": value.policy_id,
            "aliases": _aliases(value),
            "external_ids": _external_ids(value),
            "input_sha256": input_sha256,
            "record_sha256": None,
            "document_version": None,
            "status": "held",
            "reasons": [],
            "attempts": 0,
            "checkpoint_payload": None,
            "checkpoint_normalized": {},
        }
        compiled = None
        try:
            compiled = compile_source(value)
            entry["checkpoint_normalized"] = dict(compiled.normalized)
            if compiled.payload is None or compiled.reasons:
                entry["reasons"] = list(compiled.reasons) or ["source_cash_batch_compilation_held"]
            else:
                source_text = value.documents[0].text
                prepared = prepare(compiled, source_text, as_of)
                if (
                    prepared.policy_id != value.policy_id
                    or prepared.source_version != SOURCE_CASH_SOURCE_VERSION
                    or not prepared.document_version.endswith(":" + prepared.source_version)
                    or re.fullmatch(r"[0-9a-f]{64}", prepared.record_sha256) is None
                ):
                    raise ValueError("source_cash_batch_prepared_identity_invalid")
                if compiled.normalized.get("model_calls") != 0 or compiled.normalized.get(
                    "ai_review_calls"
                ) != 0:
                    raise ValueError("source_cash_batch_nonzero_model_calls")
                entry.update(
                    record_sha256=prepared.record_sha256,
                    document_version=prepared.document_version,
                    status="prepared",
                    checkpoint_payload=compiled.payload,
                )
        except Exception as error:
            diagnostic = _safe_error(error, "source_cash_batch_preparation_failed")
            entry.update(status="failed", reasons=[diagnostic["code"]], error=diagnostic)
        item = _Item(value, _aliases(value), _external_ids(value), entry, path)
        if entry["status"] == "prepared":
            item.prepared = prepared
            entry["source_sha256"] = compiled.payload["source_sha256"]
        prior = load(path)
        if prior and prior.get("input_sha256") == input_sha256 and prior.get(
            "record_sha256"
        ) == entry["record_sha256"]:
            attempts = prior.get("attempts", 0)
            entry["attempts"] = attempts if type(attempts) is int and attempts >= 0 else 0
            entry["previous_status"] = prior.get("status")
        items.append(item)
        checkpoint(item)

    selection_data = selection if isinstance(selection, Mapping) else {}
    selection_counts = selection_data.get("counts", {})
    if not isinstance(selection_counts, Mapping):
        selection_counts = {}

    def count_value(name: str, fallback: int = 0) -> int:
        value = selection_counts.get(name, fallback)
        return value if type(value) is int and value >= 0 else fallback

    def snapshot(complete: bool) -> dict:
        statuses = Counter(item.entry["status"] for item in items)
        selection_held = count_value("held")
        out_of_scope = count_value("out_of_scope")
        targets = count_value("targets", len(unique))
        source_total = count_value("source_total", targets + out_of_scope)
        counts = {
            "source_total": source_total,
            "targets": targets,
            "selected": len(unique),
            "duplicate_inputs": duplicates,
            "out_of_scope": out_of_scope,
            "selection_held": selection_held,
            "dry_run_ready": statuses["dry_run_ready"],
            "published": statuses["published"],
            "replayed": statuses["replayed"],
            "replaced": statuses["replaced"],
            "held": statuses["held"] + selection_held,
            "failed": statuses["failed"],
        }
        reasons = Counter(selection_data.get("reason_counts", {}))
        reasons.update(reason for item in items for reason in item.entry["reasons"])
        result = {
            "format_version": 1,
            "mode": "deterministic_source_cash",
            "source_version": SOURCE_CASH_SOURCE_VERSION,
            "review_metadata": SOURCE_CASH_REVIEW_METADATA,
            "model_calls": 0,
            "ai_review_calls": 0,
            "ai_verification": "not_performed",
            "dry_run": publish is None,
            "complete": complete,
            "as_of": as_of.isoformat(),
            "batch_size": batch_size,
            "selection": dict(selection_data),
            "counts": counts,
            "reason_counts": dict(sorted(reasons.items())),
            "entries": [item.entry for item in items],
        }
        save(output / "report.json", result)
        return result

    snapshot(False)

    def classify(item: _Item, identities: Sequence[Mapping[str, object]]) -> str:
        assert item.prepared is not None
        action, reason, previous = _identity_decision(
            item.prepared, item.aliases, item.external_ids, identities
        )
        item.entry["expected_previous_sha256"] = previous
        if action == "replayed":
            item.entry.update(status="replayed", reasons=[])
        elif action == "held":
            if (
                reason == "source_cash_batch_version_hash_conflict"
                and load_current_release is not None
                and timestamp_replay is not None
            ):
                try:
                    current = load_current_release(
                        item.prepared.policy_id, item.prepared.document_version
                    )
                    replay = bool(current is not None and timestamp_replay(item.prepared, current))
                except Exception as error:
                    diagnostic = _safe_error(
                        error, "source_cash_batch_current_release_lookup_failed"
                    )
                    item.entry.update(
                        status="failed", reasons=[diagnostic["code"]], error=diagnostic
                    )
                    checkpoint(item)
                    return "failed"
                if replay:
                    action = "replayed"
                    item.entry.update(
                        status="replayed",
                        reasons=["source_cash_batch_timestamp_only_replay"],
                        replay_mode="checked_at_restored",
                    )
                else:
                    item.entry.update(status="held", reasons=[reason])
            else:
                item.entry.update(status="held", reasons=[reason])
        checkpoint(item)
        return action

    def classify_one(item: _Item) -> str:
        try:
            identities = load_existing(item.aliases, item.external_ids)
        except Exception as error:
            diagnostic = _safe_error(error, "source_cash_batch_release_lookup_failed")
            item.entry.update(status="failed", reasons=[diagnostic["code"]], error=diagnostic)
            checkpoint(item)
            return "failed"
        return classify(item, identities)

    def mark_published(item: _Item, action: str) -> None:
        item.entry["status"] = "replaced" if action.startswith("replace_") else "published"
        item.entry["reasons"] = []
        item.entry.pop("batch_error", None)
        checkpoint(item)

    def send_single(item: _Item, batch_error: dict | None = None) -> None:
        assert publish is not None
        action = classify_one(item)
        if action in {"replayed", "held", "failed"}:
            return
        if batch_error is not None:
            item.entry["batch_error"] = batch_error
        item.entry["attempts"] += 1
        checkpoint(item)
        expected = {item.prepared.policy_id: item.entry["expected_previous_sha256"]} if (
            action.startswith("replace_")
        ) else {}
        try:
            result = publish(
                [item.prepared], expected, action == "replace_ai", action == "replace_unreviewed"
            )
            _validate_publish_counts(result, 1)
        except Exception as error:
            diagnostic = _safe_error(error, "source_cash_batch_publication_failed")
            item.entry["last_error"] = diagnostic
            after = classify_one(item)
            if after in {"replayed", "held"}:
                return
            item.entry.update(status="failed", reasons=[diagnostic["code"]], error=diagnostic)
            checkpoint(item)
            return
        mark_published(item, action)

    def send_group(group: list[_Item]) -> None:
        policy_ids = tuple(sorted({alias for item in group for alias in item.aliases}))
        external_ids = tuple(sorted({value for item in group for value in item.external_ids}))
        try:
            identities = load_existing(policy_ids, external_ids)
        except Exception as error:
            diagnostic = _safe_error(error, "source_cash_batch_release_lookup_failed")
            for item in group:
                item.entry.update(status="failed", reasons=[diagnostic["code"]], error=diagnostic)
                checkpoint(item)
            return

        actions = {id(item): classify(item, identities) for item in group}
        if publish is None:
            for item in group:
                if actions[id(item)] in {"new", "replace_ai", "replace_unreviewed"}:
                    item.entry.update(status="dry_run_ready", reasons=[])
                    checkpoint(item)
            return
        pending = [item for item in group if actions[id(item)] in {
            "new", "replace_ai", "replace_unreviewed"
        }]
        if not pending:
            return
        for item in pending:
            item.entry["attempts"] += 1
            checkpoint(item)
        expected = {
            item.prepared.policy_id: item.entry["expected_previous_sha256"]
            for item in pending if actions[id(item)].startswith("replace_")
        }
        try:
            result = publish(
                [item.prepared for item in pending],
                expected,
                any(actions[id(item)] == "replace_ai" for item in pending),
                any(actions[id(item)] == "replace_unreviewed" for item in pending),
            )
            _validate_publish_counts(result, len(pending))
        except Exception as error:
            diagnostic = _safe_error(error, "source_cash_batch_publication_failed")
            if len(pending) > 1:
                for item in pending:
                    send_single(item, diagnostic)
            else:
                send_single(pending[0], diagnostic)
            return
        for item in pending:
            mark_published(item, actions[id(item)])

    for offset in range(0, len(items), batch_size):
        group = items[offset : offset + batch_size]
        publishable = [item for item in group if item.prepared is not None]
        send_group(publishable)
        snapshot(False)
    return snapshot(True)


def _validate_publish_counts(result: object, expected: int) -> None:
    if not isinstance(result, Mapping):
        raise ValueError("source_cash_batch_publish_counts_invalid")
    allowed = ("published", "replayed", "replaced")
    values = [result.get(key, 0) for key in allowed]
    if any(type(value) is not int or value < 0 for value in values) or sum(values) != expected:
        raise ValueError("source_cash_batch_publish_counts_invalid")


__all__ = [
    "PreparedSourceCash",
    "SOURCE_CASH_REVIEWER_AGENT",
    "SOURCE_CASH_REVIEW_METADATA",
    "SOURCE_CASH_SOURCE_VERSION",
    "SOURCE_CASH_WRITER_AGENT",
    "run_source_cash_batch",
    "restore_checked_at_only",
    "source_cash_review_timestamp",
]
