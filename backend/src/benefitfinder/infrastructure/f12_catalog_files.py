"""Join human F12 decisions to explicitly interpreted catalogue rules, without approval."""

from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from benefitfinder.application.f12_review import f12_item_ids, latest_f12_decisions
from benefitfinder.infrastructure.policy_catalog_records import (
    CatalogRecord, Evidence, Record, validate_source_text,
)
from benefitfinder.infrastructure.policy_rule_files import (
    _read_json_object, _write_create_only, f12_approved_export,
    load_f12_decisions, load_f12_review_pair,
)
from benefitfinder.infrastructure.service_catalog_records import (
    ServiceCatalogRecord, validate_service_sources,
)


class ConditionMapping(Record):
    item_id: str
    condition_id: str


class ConflictMapping(Record):
    item_id: str
    policy_id: str


class ClauseSelection(Record):
    item_id: str
    citation_index: int = Field(ge=0)
    id: str
    title: str
    topics: list[str]


class ReferenceOnly(Record):
    item_id: str
    reason: str = Field(min_length=1, max_length=1000)


class ReleasePlan(Record):
    format_version: Literal[1]
    conditions: list[ConditionMapping] = Field(max_length=100)
    payment_items: list[str] = Field(max_length=200)
    conflicts: list[ConflictMapping] = Field(max_length=100)
    explanations: list[ClauseSelection] = Field(min_length=1, max_length=200)
    reference_only: list[ReferenceOnly] = Field(max_length=500)


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    ).encode()).hexdigest()


def prepare_f12_release(
    *, pair_dir: Path, decisions_path: Path, approved_path: Path,
    record_path: Path, plan_path: Path, source_text: str,
    price_source_text: str | None = None,
) -> CatalogRecord | ServiceCatalogRecord:
    """Require original candidates, latest decisions, exact export and a human rule plan.

    This neither infers executable operators/schedules nor sets ``benefit.reviewed``.
    The resulting entire record still requires the separate catalogue approval.
    """
    snapshot, facts = load_f12_review_pair(pair_dir)
    decisions = load_f12_decisions(decisions_path, snapshot, facts)
    expected = f12_approved_export(snapshot, facts, decisions)
    if _digest(_read_json_object(approved_path)) != _digest(expected):
        raise ValueError("f12_approved_export_mismatch")
    raw_plan = _read_json_object(plan_path)
    plan = ReleasePlan.model_validate(raw_plan)
    if type(raw_plan.get("format_version")) is not int:
        raise ValueError("invalid_f12_plan_version")
    data = _read_json_object(record_path)
    if data.get("f12_review") is not None or data.get("explanation_clauses") is not None:
        raise ValueError("f12_release_already_prepared")
    model = ServiceCatalogRecord if data.get("kind") == "service" else CatalogRecord
    record = model.model_validate(data)
    source = record.benefit.source.model_dump(mode="json")
    document = facts.document
    policy = next((p for p in snapshot.batch.policies if p.id == document.policy_id), None)
    if (policy is None or policy.domain != record.benefit.domain
        or list(policy.categories) != record.benefit.categories):
        raise ValueError("f12_catalog_classification_mismatch")
    if (
        record.benefit.id != document.policy_id
        or source["url"] != document.source_url
        or source["document_version"] != document.document_version
        or datetime.fromisoformat(source["checked_at"].replace("Z", "+00:00")) != document.collected_at
        or record.source_sha256 != document.content_sha256 or source_text != document.text
    ):
        raise ValueError("f12_catalog_source_mismatch")
    check = validate_service_sources if isinstance(record, ServiceCatalogRecord) else validate_source_text
    if isinstance(record, ServiceCatalogRecord):
        check(record, source_text, price_source_text)
    else:
        check(record, source_text)

    approved = {item["item_id"]: item for item in expected["items"]}
    used: set[str] = set()

    def item(item_id: str, kind: str | None = None) -> dict:
        candidate = approved.get(item_id)
        if candidate is None or (kind is not None and candidate["kind"] != kind):
            raise ValueError("f12_mapping_requires_approved_item")
        used.add(item_id)
        return candidate["candidate"]

    def evidence(candidate: dict) -> list[dict]:
        citations = candidate.get("citations", [candidate.get("evidence")])
        return [Evidence(quote=c["quote"], location=c["location"], source=source).model_dump(mode="json")
                for c in citations]

    conditions = {c.id: c for c in record.benefit.eligibility_conditions}
    mapped_conditions: set[str] = set()
    for mapping in plan.conditions:
        selected = evidence(item(mapping.item_id, "eligibility"))
        condition = conditions.get(mapping.condition_id)
        if (condition is None or mapping.condition_id in mapped_conditions
            or [e.model_dump(mode="json") for e in condition.evidence] != selected):
            raise ValueError("f12_condition_mapping_mismatch")
        mapped_conditions.add(mapping.condition_id)
    if mapped_conditions != set(conditions):
        raise ValueError("f12_unmapped_conditions")

    mapped_conflicts: set[str] = set()
    conflicts = {c.policy_id: c for c in record.benefit.conflicts}
    for mapping in plan.conflicts:
        candidate = item(mapping.item_id, "rule")
        if (candidate["kind"] != "simultaneous_exclusion" or candidate["direction"] != "both"
            or candidate["target_policy_ids"] != [mapping.policy_id]
            or candidate["source_policy_id"] != record.benefit.id
            or any(candidate[key] for key in ("conditions", "exceptions", "period", "amount_effect", "unresolved"))):
            raise ValueError("f12_rule_requires_manual_resolution")
        conflict = conflicts.get(mapping.policy_id)
        if (conflict is None or mapping.policy_id in mapped_conflicts
            or [e.model_dump(mode="json") for e in conflict.evidence] != evidence(candidate)):
            raise ValueError("f12_conflict_mapping_mismatch")
        mapped_conflicts.add(mapping.policy_id)
    if mapped_conflicts != set(conflicts):
        raise ValueError("f12_unmapped_conflicts")

    if len(set(plan.payment_items)) != len(plan.payment_items):
        raise ValueError("f12_duplicate_payment_item")
    payment_evidence = []
    payment_kinds = set()
    for item_id in plan.payment_items:
        candidate = item(item_id)
        kind = approved[item_id]["kind"]
        if isinstance(record, ServiceCatalogRecord) or kind not in {"amount", "timing"}:
            raise ValueError("f12_invalid_payment_mapping")
        if kind == "amount" and record.benefit.benefit_amount is not None:
            if record.benefit.benefit_amount.amount_won not in candidate["amounts_won"]:
                raise ValueError("f12_amount_interpretation_mismatch")
        payment_kinds.add(kind)
        payment_evidence.extend(evidence(candidate))
    if isinstance(record, CatalogRecord):
        if (set(map(_digest, payment_evidence)) != set(_digest(e.model_dump(mode="json")) for e in record.payment_evidence)
            or (record.benefit.reviewed and payment_kinds != {"amount", "timing"})):
            raise ValueError("f12_payment_mapping_mismatch")
        if not {
            _digest(e.model_dump(mode="json")) for e in record.maturity_accrual_evidence or []
        } <= set(map(_digest, payment_evidence)):
            raise ValueError("f12_maturity_accrual_mapping_mismatch")

    # Explanations may reuse a mapped item, but never count as resolving its rule.
    resolved = set(used)
    clauses = []
    for selection in plan.explanations:
        choices = evidence(item(selection.item_id))
        if selection.citation_index >= len(choices):
            raise ValueError("f12_citation_index_out_of_range")
        clauses.append({"id": selection.id, "title": selection.title, "topics": selection.topics,
                        "evidence": choices[selection.citation_index]})
    references = [reference.item_id for reference in plan.reference_only]
    if (len(set(references)) != len(references) or set(references) & resolved
        or any(not reference.reason.strip() for reference in plan.reference_only)):
        raise ValueError("invalid_f12_reference_only")
    for reference in plan.reference_only:
        item(reference.item_id)
    if used != set(approved) or resolved | set(references) != set(approved):
        raise ValueError("f12_unaccounted_approved_items")
    all_ids = {value for group in f12_item_ids(snapshot, facts).values() for value in group}
    latest = latest_f12_decisions(decisions)
    unresolved = (all_ids - set(latest)) | {
        key for key in approved if approved[key]["kind"] in {"rule", "eligibility"} and key not in resolved
    }
    if unresolved and (isinstance(record, ServiceCatalogRecord)
                       or record.benefit.reviewed or not record.unresolved_combination_rules):
        raise ValueError("f12_unresolved_candidates_require_manual_review")
    data["explanation_clauses"] = clauses
    data["f12_review"] = {
        "pair_sha256": expected["pair_sha256"],
        "decisions_sha256": _digest([{**asdict(d), "reviewed_at": d.reviewed_at.isoformat()} for d in decisions]),
        "export_sha256": _digest(expected), "plan_sha256": _digest(raw_plan),
        "source_document_id": document.id,
        "latest_reviewed_at": max(d.reviewed_at for d in decisions).isoformat(),
    }
    return model.model_validate(data)


def write_f12_catalog(path: Path, record: CatalogRecord | ServiceCatalogRecord) -> None:
    """Create one private local draft; never overwrite, publish or issue approval."""
    _write_create_only(path, record.model_dump(mode="json"))
