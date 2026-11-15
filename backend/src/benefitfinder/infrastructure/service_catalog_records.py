"""Reviewed, source-bound records for non-cash F14 service releases.

Service records reuse the executable youth conditions and source rules used by
the cash catalogue. A service price is retained only as a reviewed basis and
unit value; public totals are calculated from the published record in code.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from benefitfinder.application.published_services import PublishedService
from benefitfinder.application.service_catalog import ServicePrice
from benefitfinder.infrastructure.policy_catalog_records import (
    MAX_WON,
    Application,
    CollectionSource,
    Condition,
    Conflict,
    Evidence,
    ExecutableCondition,
    Record,
    Source,
)


class ServiceContent(Record):
    item: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    quantity: int = Field(ge=1, le=MAX_WON)

    @model_validator(mode="after")
    def validate_content(self) -> ServiceContent:
        if not self.item.strip() or not self.unit.strip():
            raise ValueError("empty_service_item_or_unit")
        return self


class ServiceUnitPrice(Record):
    unit_value_won: int = Field(ge=0, le=MAX_WON)
    basis: str = Field(min_length=1, max_length=500)
    source: Source
    quote: str = Field(min_length=1, max_length=2500)
    location: str = Field(min_length=1, max_length=500)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_review_material(self) -> ServiceUnitPrice:
        if not self.basis.strip():
            raise ValueError("empty_price_basis")
        if not self.quote.strip() or not self.location.strip():
            raise ValueError("empty_price_quote_or_location")
        return self


class ServiceBenefit(Record):
    id: str = Field(pattern=r"^youth:[A-Za-z0-9:_-]{1,100}$")
    domain: Literal["youth"]
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    categories: list[Literal["employment", "housing"]] = Field(min_length=1, max_length=2)
    application: Application
    eligibility_conditions: list[Condition] = Field(max_length=100)
    source: Source
    domain_data: dict[str, Any]
    virtual_data: bool = False
    conflicts: list[Conflict] = Field(default_factory=list)
    service: ServiceContent

    @model_validator(mode="after")
    def validate_public_text(self) -> ServiceBenefit:
        if not self.title.strip() or not self.title.isprintable():
            raise ValueError("invalid_service_title")
        if not self.summary.strip() or not self.provider.strip():
            raise ValueError("missing_service_summary_or_provider")
        if len(set(self.categories)) != len(self.categories):
            raise ValueError("duplicate_category")
        return self


class ServiceCatalogRecord(Record):
    kind: Literal["service"]
    benefit: ServiceBenefit
    conditions: list[ExecutableCondition]
    service_evidence: list[Evidence] = Field(min_length=1)
    price: ServiceUnitPrice | None
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    collection_sources: list[CollectionSource] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_record(self) -> ServiceCatalogRecord:
        benefit = self.benefit
        origin_keys = [(item.source, item.external_id) for item in self.collection_sources]
        if len(origin_keys) != len(set(origin_keys)):
            raise ValueError("duplicate_collection_source")
        if not benefit.virtual_data and not origin_keys:
            raise ValueError("real_service_requires_collection_sources")

        condition_ids = [condition.id for condition in benefit.eligibility_conditions]
        rule_ids = [rule.condition_id for rule in self.conditions]
        if (
            len(set(condition_ids)) != len(condition_ids)
            or len(set(rule_ids)) != len(rule_ids)
            or set(condition_ids) != set(rule_ids)
        ):
            raise ValueError("condition_rule_mismatch")

        evidence = list(self.service_evidence)
        evidence += [
            item for condition in benefit.eligibility_conditions for item in condition.evidence
        ]
        evidence += [item for conflict in benefit.conflicts for item in conflict.evidence]
        if any(item.source != benefit.source for item in evidence):
            raise ValueError("evidence_source_mismatch")
        if any(conflict.policy_id == benefit.id for conflict in benefit.conflicts):
            raise ValueError("self_conflict")
        return self

    def published(self, published_at: datetime) -> PublishedService:
        price = None
        if self.price is not None:
            checked_at = datetime.fromisoformat(
                self.price.source.checked_at.replace("Z", "+00:00")
            ).date()
            price = ServicePrice(
                unit_value_won=self.price.unit_value_won,
                basis=self.price.basis,
                url=self.price.source.url,
                checked_at=checked_at,
                reviewed=True,
            )
        return PublishedService(
            detail=self.benefit.model_dump(mode="json"),
            conditions=tuple(
                rule.model_dump(exclude_none=True) for rule in self.conditions
            ),
            price=price,
            published_at=published_at.isoformat(),
        )


def service_record_hash(record: ServiceCatalogRecord) -> str:
    """Return the canonical digest of every approved service metadata field."""
    canonical = json.dumps(
        record.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def validate_service_sources(
    record: ServiceCatalogRecord,
    source_text: str,
    price_source_text: str | None = None,
) -> None:
    """Verify the notice and every exact citation, including a separate price source."""
    if hashlib.sha256(source_text.encode("utf-8")).hexdigest() != record.source_sha256:
        raise ValueError("source_hash_mismatch")
    evidence = list(record.service_evidence)
    evidence += [
        item
        for condition in record.benefit.eligibility_conditions
        for item in condition.evidence
    ]
    evidence += [item for conflict in record.benefit.conflicts for item in conflict.evidence]
    if any(item.quote not in source_text for item in evidence):
        raise ValueError("quote_missing_from_source")

    price = record.price
    if price is None:
        if price_source_text is not None:
            raise ValueError("unexpected_price_source_text")
        return
    if price_source_text is None:
        raise ValueError("price_source_required")
    if hashlib.sha256(price_source_text.encode("utf-8")).hexdigest() != price.source_sha256:
        raise ValueError("price_source_hash_mismatch")
    if price.quote not in price_source_text:
        raise ValueError("price_quote_missing_from_source")
