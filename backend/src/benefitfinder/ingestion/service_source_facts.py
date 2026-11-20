"""Bind structured OnTong Youth facts to a separately written service record.

A service record's own conditions come from reading the notice body, so the
structured residence (``zipCd``) and age fields can be missing from it. Without a
residence rule the S6 service list cannot exclude a service limited to another
region. This step adds only executable rules that the source projection proves,
keeps every existing condition, and marks an explicitly closed application
(``aplyPrdSeCd=0057003``) as closed. It reads no network or database; the
caller supplies the official region catalogue and publishes the result
separately.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

from benefitfinder.application.region_options import RegionOption
from benefitfinder.infrastructure.service_catalog_records import ServiceCatalogRecord
from benefitfinder.ingestion.source_eligibility import (
    compile_source_restrictions,
    projection_values,
)

CLOSED_APPLICATION_CODE = "0057003"
_REGION_FIELDS = frozenset({"region_code", "domain_data.region.sido_code"})


@dataclass(frozen=True)
class ServiceSourceFacts:
    record: ServiceCatalogRecord
    added_condition_ids: tuple[str, ...]
    closed: bool

    @property
    def changed(self) -> bool:
        return bool(self.added_condition_ids) or self.closed


def bind_service_source_facts(
    record: ServiceCatalogRecord,
    source_text: str,
    *,
    regions: Sequence[RegionOption],
) -> ServiceSourceFacts:
    """Return the record with proven residence/age rules and closed status added."""

    unchanged = ServiceSourceFacts(record, (), False)
    if record.benefit.virtual_data:
        return unchanged
    bindings = record.collection_sources
    if len(bindings) != 1 or bindings[0].source != "youthcenter":
        return unchanged
    if hashlib.sha256(source_text.encode("utf-8")).hexdigest() != record.source_sha256:
        raise ValueError("source_hash_mismatch")

    data = record.model_dump(mode="json")
    benefit = data["benefit"]
    existing_rules = data["conditions"]
    has_region = any(
        rule.get("kind") == "selection" and rule.get("field") in _REGION_FIELDS
        for rule in existing_rules
    )
    has_age = any(rule.get("kind") == "age" for rule in existing_rules)
    used_ids = {condition["id"] for condition in benefit["eligibility_conditions"]}

    conditions, rules = compile_source_restrictions(
        source_text, benefit["source"], regions=regions
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
    return ServiceSourceFacts(ServiceCatalogRecord.model_validate(data), tuple(added), closed)
