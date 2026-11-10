"""Read local F12 v1 artifacts for evaluation; no dependency on unmerged extractor code."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import NoReturn

from benefitfinder.application.f12_evaluation import EvaluationCase, EvaluationItem, evaluate_cases
from benefitfinder.application.policy_rules import (
    ReviewOperation, RuleCandidate, RuleCitation, RuleDirection, RuleKind, SourceDocument,
)
from benefitfinder.infrastructure.policy_rule_files import RuleReviewFileError, load_rule_snapshot


class EvaluationFileError(ValueError):
    """Fixed code only; never include source text, paths, or reviewer data."""


def _invalid() -> NoReturn:
    raise EvaluationFileError("invalid_f12_evaluation_input")


def _object(value: object, keys: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        _invalid()
    return value


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        _invalid()
    return value


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > 100:
        _invalid()
    return tuple(_text(x) for x in value)


def _time(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(_text(value))
        if parsed.utcoffset() is None:
            _invalid()
        return parsed
    except ValueError:
        _invalid()


def _hash(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        _invalid()
    return value


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            _invalid()
        result[key] = value
    return result


def _constant(value: str) -> NoReturn:
    _invalid()


def _read(path: Path) -> dict[str, object]:
    try:
        if path.stat().st_size > 32 * 1024 * 1024:
            _invalid()
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique,
                          parse_constant=_constant)
        if not isinstance(data, dict) or type(data.get("format_version")) is not int:
            _invalid()
        if data["format_version"] != 1:
            _invalid()
        return data
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        _invalid()


def _citation(raw: object) -> RuleCitation:
    c = _object(raw, {"document_id", "document_version", "content_sha256", "quote", "location"})
    return RuleCitation(_text(c["document_id"]), _text(c["document_version"]),
                        _hash(c["content_sha256"]), _text(c["quote"]), _text(c["location"]))


def _source(raw: object, document: SourceDocument) -> None:
    s = _object(raw, {"id", "url", "document_version", "content_sha256", "collected_at"})
    if (s["id"], s["url"], s["document_version"], _hash(s["content_sha256"])) != (
        document.id, document.source_url, document.document_version, document.content_sha256
    ) or _time(s["collected_at"]) != document.collected_at:
        _invalid()


def _rule(raw: object, item_id: str, document: SourceDocument) -> EvaluationItem:
    r = _object(raw, {"id", "source_policy_id", "target_policy_ids", "kind", "description",
                      "citations", "direction", "conditions", "exceptions", "period",
                      "amount_effect", "unresolved"})
    _text(r["id"])
    _text(r["description"])
    if r["source_policy_id"] != document.policy_id:
        _invalid()
    targets = _strings(r["target_policy_ids"])
    if not targets or len(targets) != len(set(targets)):
        _invalid()
    kind, direction = RuleKind(r["kind"]), RuleDirection(r["direction"])
    _strings(r["conditions"])
    _strings(r["unresolved"])
    for name in ("period", "amount_effect"):
        if r[name] is not None:
            _text(r[name])
    if not isinstance(r["citations"], list):
        _invalid()
    return EvaluationItem(item_id, "rule", tuple(_citation(c) for c in r["citations"]),
                          targets, kind.value, direction.value, _strings(r["exceptions"]))


def _from_rule(candidate: RuleCandidate, index: int, document: SourceDocument) -> EvaluationItem:
    if candidate.source_policy_id != document.policy_id:
        _invalid()
    return EvaluationItem(f"rule:{index}", "rule", candidate.citations,
                          candidate.target_policy_ids, candidate.kind.value,
                          candidate.direction.value, candidate.exceptions)


def _fact(raw: object, item_id: str, kind: str) -> EvaluationItem:
    item = _object(raw, {"evidence", "amounts_won"} if kind == "amount" else {"evidence"})
    amounts: tuple[int, ...] = ()
    if kind == "amount":
        values = item["amounts_won"]
        if (not isinstance(values, list) or len(values) > 100
                or any(type(x) is not int or x < 0 for x in values)):
            _invalid()
        amounts = tuple(sorted(values))
    return EvaluationItem(item_id, kind, (_citation(item["evidence"]),), amounts_won=amounts)


def _document(directory: Path, category: str) -> tuple[SourceDocument, tuple[EvaluationItem, ...]]:
    snapshot = load_rule_snapshot(directory / "rules-pending.json")
    if snapshot.operation != ReviewOperation.PREPARED or len(snapshot.batch.documents) != 1:
        _invalid()
    (document,) = snapshot.batch.documents
    if document.document_version.startswith("sha256:") and (
        document.document_version != "sha256:" + document.content_sha256
    ):
        _invalid()
    policies = [p for p in snapshot.batch.policies if p.id == document.policy_id]
    if len(policies) != 1 or policies[0].domain != "youth" or category not in policies[0].categories:
        _invalid()
    if tuple(r.candidate for r in snapshot.reviews) != snapshot.batch.candidates:
        _invalid()
    if any(r.approval is not None for r in snapshot.reviews):
        _invalid()
    items = tuple(_from_rule(c, i, document) for i, c in enumerate(snapshot.batch.candidates, 1))
    return document, items


def _candidate_facts(directory: Path, document: SourceDocument) -> tuple[EvaluationItem, ...]:
    d = _object(_read(directory / "facts-pending.json"), {
        "format_version", "policy_id", "source", "status", "eligibility", "benefit_amount",
        "payment_timing",
    })
    if d["policy_id"] != document.policy_id or d["status"] != "needs_review":
        _invalid()
    _source(d["source"], document)
    result = []
    for name, kind in (("eligibility", "eligibility"), ("benefit_amount", "amount"),
                       ("payment_timing", "timing")):
        keys = {"status", "items", "scheduled_monthly_day"} if kind == "timing" else {"status", "items"}
        section = _object(d[name], keys)
        raw_items = section["items"]
        if (not isinstance(raw_items, list) or len(raw_items) > 100
                or section["status"] != ("extracted" if raw_items else "not_found")):
            _invalid()
        if kind == "timing":
            day = section["scheduled_monthly_day"]
            if day is not None and (type(day) is not int or not 1 <= day <= 31 or not raw_items):
                _invalid()
        result.extend(_fact(x, f"{kind}:{i}", kind) for i, x in enumerate(raw_items, 1))
    return tuple(result)


def _approved(path: Path, document: SourceDocument) -> tuple[EvaluationItem, ...]:
    d = _object(_read(path), {"format_version", "kind", "pair_sha256", "policy_id", "source", "items"})
    if d["kind"] != "f12_approved_review_export" or d["policy_id"] != document.policy_id:
        _invalid()
    _hash(d["pair_sha256"])
    _source(d["source"], document)
    if not isinstance(d["items"], list) or not 1 <= len(d["items"]) <= 100:
        _invalid()
    result = []
    for raw in d["items"]:
        item = _object(raw, {"item_id", "kind", "approval", "candidate"})
        item_id, kind = _text(item["item_id"]), item["kind"]
        if kind not in {"rule", "eligibility", "amount", "timing"} or not re.fullmatch(
            re.escape(kind) + r":[1-9][0-9]*", item_id
        ):
            _invalid()
        approval = _object(item["approval"], {"reviewer", "reason", "reviewed_at"})
        _text(approval["reviewer"])
        _text(approval["reason"])
        if _time(approval["reviewed_at"]) < document.collected_at:
            _invalid()
        result.append(_rule(item["candidate"], item_id, document) if kind == "rule"
                      else _fact(item["candidate"], item_id, kind))
    return tuple(result)


def load_evaluation_cases(manifest: Path) -> tuple[EvaluationCase, ...]:
    """Read v1 case manifest and existing F12 pairs/approved exports without writing."""
    try:
        d = _object(_read(manifest), {"format_version", "cases"})
        if not isinstance(d["cases"], list) or not 1 <= len(d["cases"]) <= 100:
            _invalid()
        result = []
        for raw in d["cases"]:
            keys = {"id", "category", "reference_dir", "approved_exports", "candidate_dir"}
            if not isinstance(raw, dict) or set(raw) not in (keys, keys | {"candidate_error"}):
                _invalid()
            c = raw
            category = c["category"]
            if category not in {"employment", "housing"}:
                _invalid()
            reference_dir = (manifest.parent / _text(c["reference_dir"])).resolve()
            reference, _ = _document(reference_dir, category)
            if not reference.official:
                _invalid()
            error = c.get("candidate_error")
            if error is not None:
                if c["candidate_dir"] is not None or error not in {
                    "context_limit", "model_failure", "extraction_failed"
                }:
                    _invalid()
                candidate, candidates = None, ()
            else:
                candidate_dir = (manifest.parent / _text(c["candidate_dir"])).resolve()
                if reference_dir == candidate_dir:
                    _invalid()
                candidate, rules = _document(candidate_dir, category)
                candidates = rules + _candidate_facts(candidate_dir, candidate)
            exports = _strings(c["approved_exports"])
            if not exports:
                _invalid()
            approved = tuple(i for path in exports for i in _approved(
                (manifest.parent / path).resolve(), reference
            ))
            result.append(EvaluationCase(_text(c["id"]), category, reference, candidate,
                                         approved, candidates, error))
        return tuple(result)
    except (RuleReviewFileError, ValueError, TypeError, KeyError, RecursionError):
        _invalid()


def evaluate_manifest(manifest: Path) -> dict[str, object]:
    """Build a local report, exposing no original content through exceptions."""
    try:
        return evaluate_cases(load_evaluation_cases(manifest))
    except (ValueError, TypeError, KeyError, RecursionError):
        _invalid()


def write_evaluation_report(path: Path, report: dict[str, object]) -> None:
    """Create only; a failed write never replaces any prior report or review artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(dir=path.parent, prefix=".f12-evaluation-", delete=False) as f:
            temporary = Path(f.name)
            f.write((json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
