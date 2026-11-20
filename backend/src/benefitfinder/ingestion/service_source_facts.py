"""Bind structured OnTong Youth facts to a separately written service record.

A service record's own conditions come from reading the notice body, so the
structured residence (``zipCd``) and age fields can be missing from it. Without a
residence rule the S6 service list cannot exclude a service limited to another
region. This step adds only executable rules that the source projection proves,
keeps every existing condition, and marks an explicitly closed application
(``aplyPrdSeCd=0057003``) as closed. A changed record gets the
``:source-facts-v1`` document version so it can be published as a new release
beside the retired original instead of conflicting with it. It reads no network
or database; the caller supplies the official region catalogue, validates the
returned payload as a service catalogue record, and publishes it separately.
"""

from __future__ import annotations

import copy
import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from benefitfinder.application.region_options import RegionOption
from benefitfinder.ingestion.source_eligibility import (
    compile_source_restrictions,
    projection_values,
)

CLOSED_APPLICATION_CODE = "0057003"
_REGION_FIELDS = frozenset({"region_code", "domain_data.region.sido_code"})
SOURCE_FACTS_SUFFIX = ":source-facts-v1"


def _source_facts_version(current: str, material_sha256: str) -> str:
    base = "sha256:" + material_sha256
    if current == base + SOURCE_FACTS_SUFFIX:
        raise ValueError("service_source_facts_already_bound")
    if not re.fullmatch(re.escape(base) + r"(?::decider-v3)?", current):
        raise ValueError("service_source_facts_unsupported_document_version")
    return base + SOURCE_FACTS_SUFFIX


def _replace_source(value: object, old: dict, new: dict) -> object:
    if isinstance(value, dict):
        if value == old:
            return new.copy()
        return {key: _replace_source(item, old, new) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_source(item, old, new) for item in value]
    return value


@dataclass(frozen=True)
class ServiceSourceFacts:
    payload: dict[str, Any]
    added_condition_ids: tuple[str, ...]
    closed: bool

    @property
    def changed(self) -> bool:
        return bool(self.added_condition_ids) or self.closed


def bind_service_source_facts(
    payload: Mapping[str, Any],
    source_text: str,
    *,
    regions: Sequence[RegionOption],
) -> ServiceSourceFacts:
    """Return a service payload with proven residence/age rules and closed status added.

    ``payload`` is a JSON dump of one service catalogue record; the input is never
    mutated. An unchanged result returns an equal copy of the input.
    """

    data = copy.deepcopy(dict(payload))
    unchanged = ServiceSourceFacts(copy.deepcopy(data), (), False)
    if data.get("kind") != "service":
        raise ValueError("service_source_facts_requires_service_record")
    benefit = data["benefit"]
    if benefit.get("virtual_data"):
        return unchanged
    bindings = data.get("collection_sources") or []
    if len(bindings) != 1 or bindings[0].get("source") != "youthcenter":
        return unchanged
    if hashlib.sha256(source_text.encode("utf-8")).hexdigest() != data.get("source_sha256"):
        raise ValueError("source_hash_mismatch")

    existing_rules = data["conditions"]
    has_region = any(
        rule.get("kind") == "selection" and rule.get("field") in _REGION_FIELDS
        for rule in existing_rules
    )
    has_age = any(rule.get("kind") == "age" for rule in existing_rules)
    used_ids = {condition["id"] for condition in benefit["eligibility_conditions"]}

    conditions, rules = compile_source_restrictions(
        source_text, dict(benefit["source"]), regions=regions
    )
    added: list[str] = []
    for condition, rule in zip(conditions, rules, strict=True):
        if rule["kind"] == "age" and has_age:
            continue
        if rule["kind"] == "selection" and has_region:
            continue
        condition_id = "source-facts:" + condition["id"]
        if condition_id in used_ids:
            continue
        condition["id"] = condition_id
        rule["condition_id"] = condition_id
        benefit["eligibility_conditions"].append(condition)
        existing_rules.append(rule)
        used_ids.add(condition_id)
        added.append(condition_id)

    application = benefit["application"]
    closed = (
        projection_values(source_text).get("aplyPrdSeCd", "").strip() == CLOSED_APPLICATION_CODE
        and application["status"] != "closed"
    )
    if closed:
        application["status"] = "closed"

    if not added and not closed:
        return unchanged
    old_source = dict(benefit["source"])
    new_source = {**old_source, "document_version": _source_facts_version(
        old_source["document_version"], bindings[0]["material_sha256"])}
    return ServiceSourceFacts(_replace_source(data, old_source, new_source), tuple(added), closed)
