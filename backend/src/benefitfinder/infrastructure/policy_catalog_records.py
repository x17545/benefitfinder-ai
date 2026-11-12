"""Validate human-curated catalogue releases before import and after DB reads.

This format supports a deliberately small set of executable rules. A clause
that cannot be expressed safely remains a manual condition or an unreviewed
policy. Agent review exports are not accepted as published releases.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from benefitfinder.application.published_policies import PublishedPolicy

MAX_WON = 2**53 - 1


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Source(Record):
    url: str = Field(min_length=1, max_length=2048)
    document_version: str = Field(min_length=1, max_length=120)
    checked_at: str

    @model_validator(mode="after")
    def validate_source(self):
        if not self.document_version.strip():
            raise ValueError("empty_document_version")
        url = urlsplit(self.url)
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("invalid_source_url")
        if any(char.isspace() for char in self.url):
            raise ValueError("invalid_source_url")
        if datetime.fromisoformat(self.checked_at.replace("Z", "+00:00")).utcoffset() is None:
            raise ValueError("source_requires_timezone")
        return self


class Evidence(Record):
    quote: str = Field(min_length=1, max_length=2500)
    location: str = Field(min_length=1, max_length=500)
    source: Source


class ApplicationGuide(Record):
    method: Evidence | None
    office: Evidence | None
    # None means not checked; [] is a human-confirmed absence in this source.
    documents: list[Evidence] | None = Field(max_length=100)

    def citations(self) -> list[Evidence]:
        return [item for item in (self.method, self.office) if item is not None] + (
            self.documents or []
        )

    @model_validator(mode="after")
    def validate_citations(self) -> ApplicationGuide:
        if any(not item.quote.strip() or not item.location.strip() for item in self.citations()):
            raise ValueError("empty_application_guide_citation")
        return self


class Condition(Record):
    id: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1)
    evidence: list[Evidence] = Field(min_length=1)


class Conflict(Record):
    policy_id: str = Field(pattern=r"^youth:[A-Za-z0-9:_-]{1,100}$")
    evidence: list[Evidence] = Field(min_length=1)


class Application(Record):
    status: Literal["open", "closed", "upcoming", "unknown"]
    starts_on: str | None
    ends_on: str | None
    url: str | None

    @model_validator(mode="after")
    def validate_dates(self):
        start = date.fromisoformat(self.starts_on) if self.starts_on is not None else None
        end = date.fromisoformat(self.ends_on) if self.ends_on is not None else None
        if start is not None and end is not None and start > end:
            raise ValueError("application_dates_reversed")
        if self.url is not None:
            url = urlsplit(self.url)
            if (
                url.scheme not in ("http", "https")
                or not url.hostname
                or url.username
                or url.password
                or any(c.isspace() for c in self.url)
            ):
                raise ValueError("invalid_application_url")
        return self


class Amount(Record):
    type: Literal["monthly", "lump_sum", "maturity", "interest"] | None = None
    amount_won: int | None = Field(default=None, ge=0, le=MAX_WON)
    period_months: int | None = Field(default=None, ge=1, le=1200)
    max_total_won: int | None = Field(default=None, ge=0, le=MAX_WON)
    pay_timing: Literal["monthly_from_start", "at_maturity", "yearly", "once"] | None = None
    amount_text: str | None = None
    amount_status: Literal["verified", "unknown"] = "unknown"


class Benefit(Record):
    id: str = Field(pattern=r"^youth:[A-Za-z0-9:_-]{1,100}$")
    domain: Literal["youth"]
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    categories: list[Literal["employment", "housing"]] = Field(min_length=1, max_length=2)
    application: Application
    application_guide: ApplicationGuide | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    eligibility_conditions: list[Condition] = Field(max_length=100)
    source: Source
    domain_data: dict[str, Any]
    benefit_amount: Amount | None = None
    reviewed: bool = False
    conflicts: list[Conflict] = Field(default_factory=list)
    lifetime_once: bool | None = None
    virtual_data: bool = False

    @model_validator(mode="after")
    def validate_public_text(self):
        if not self.title.strip() or not self.title.isprintable():
            raise ValueError("invalid_policy_title")
        if not self.summary.strip() or not self.provider.strip():
            raise ValueError("missing_policy_summary_or_provider")
        return self


SELECTION_FIELDS = {
    "employment_status",
    "region_code",
    "domain_data.region.sido_code",
    "domain_data.school",
    "domain_data.marital",
    "domain_data.housing.living",
    "domain_data.housing.home_ownership",
    "domain_data.housing.household_head",
    "domain_data.contract.type",
    "domain_data.asset_band",
    "domain_data.special",
    "domain_data.received",
}


class ExecutableCondition(Record):
    condition_id: str = Field(min_length=1)
    kind: Literal["manual", "age", "income_max", "selection", "none_of"]
    minimum: int | None = Field(default=None, ge=0)
    maximum: int | None = Field(default=None, ge=0, le=MAX_WON)
    military_extension_years: int | None = Field(default=None, ge=0, le=3)
    inclusive: bool | None = None
    field: str | None = None
    values: list[str] | None = None

    @model_validator(mode="after")
    def validate_operation(self):
        supplied = {
            key
            for key in self.model_fields_set - {"condition_id", "kind"}
            if getattr(self, key) is not None
        }
        expected = {
            "manual": set(),
            "age": {"minimum", "maximum", "military_extension_years"},
            "income_max": {"maximum", "inclusive"},
            "selection": {"field", "values"},
            "none_of": {"field", "values"},
        }[self.kind]
        if supplied != expected or any(getattr(self, key) is None for key in expected):
            raise ValueError("incomplete_or_unsupported_condition")
        if self.kind == "age" and self.minimum > self.maximum:
            raise ValueError("invalid_age_range")
        if self.kind in ("selection", "none_of"):
            if (
                self.field not in SELECTION_FIELDS
                or not self.values
                or len(set(self.values)) != len(self.values)
            ):
                raise ValueError("invalid_selection_rule")
        return self


class Payment(Record):
    month: int = Field(ge=0, le=1200)
    amount_won: int = Field(ge=0, le=MAX_WON)


class CollectionSource(Record):
    source: Literal["youthcenter", "gov24"]
    external_id: str = Field(min_length=1, max_length=200)
    material_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_id(self) -> CollectionSource:
        if not self.external_id.strip() or "\0" in self.external_id:
            raise ValueError("invalid_collection_source_id")
        return self


class CatalogRecord(Record):
    benefit: Benefit
    conditions: list[ExecutableCondition]
    payments: list[Payment] | None = Field(max_length=1200)
    payment_evidence: list[Evidence]
    unresolved_combination_rules: bool
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    collection_sources: list[CollectionSource] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_record(self):
        benefit = self.benefit
        sources = [(item.source, item.external_id) for item in self.collection_sources]
        if len(sources) != len(set(sources)):
            raise ValueError("duplicate_collection_source")
        if not benefit.virtual_data and not sources:
            raise ValueError("real_policy_requires_collection_sources")
        ids = [condition.id for condition in benefit.eligibility_conditions]
        rule_ids = [rule.condition_id for rule in self.conditions]
        if (
            len(set(ids)) != len(ids)
            or len(set(rule_ids)) != len(rule_ids)
            or set(ids) != set(rule_ids)
        ):
            raise ValueError("condition_rule_mismatch")
        if len(set(benefit.categories)) != len(benefit.categories):
            raise ValueError("duplicate_category")
        evidence = [
            item for condition in benefit.eligibility_conditions for item in condition.evidence
        ]
        evidence += [item for conflict in benefit.conflicts for item in conflict.evidence]
        evidence += self.payment_evidence
        if benefit.application_guide is not None:
            evidence += benefit.application_guide.citations()
        if any(item.source != benefit.source for item in evidence):
            raise ValueError("evidence_source_mismatch")
        if any(c.policy_id == benefit.id for c in benefit.conflicts):
            raise ValueError("self_conflict")
        if benefit.reviewed:
            amount = benefit.benefit_amount
            if (
                self.unresolved_combination_rules
                or not self.payments
                or not self.payment_evidence
                or amount is None
                or amount.amount_status != "verified"
                or amount.pay_timing is None
            ):
                raise ValueError("reviewed_policy_requires_verified_calculation")
            total = sum(payment.amount_won for payment in self.payments)
            if total > MAX_WON or amount.max_total_won != total:
                raise ValueError("payment_total_mismatch")
            # Accrued maturity matching is not yet a supported nominal schedule.
            if amount.type == "maturity" or amount.pay_timing == "at_maturity":
                raise ValueError("maturity_requires_separate_accrual_review")
            schedule = sorted((p.month, p.amount_won) for p in self.payments)
            if amount.type is None or amount.amount_won is None:
                raise ValueError("reviewed_policy_requires_amount_metadata")
            if amount.pay_timing == "monthly_from_start":
                if (
                    amount.type != "monthly"
                    or amount.period_months is None
                    or schedule
                    != [(month, amount.amount_won) for month in range(1, amount.period_months + 1)]
                ):
                    raise ValueError("monthly_schedule_mismatch")
            elif amount.pay_timing == "once":
                if (
                    amount.type != "lump_sum"
                    or len(schedule) != 1
                    or schedule[0][1] != amount.amount_won
                ):
                    raise ValueError("lump_sum_schedule_mismatch")
            elif amount.pay_timing == "yearly":
                if (
                    amount.type not in ("interest", "lump_sum")
                    or amount.period_months is None
                    or amount.period_months % 12 != 0
                    or schedule
                    != [
                        (month, amount.amount_won)
                        for month in range(12, amount.period_months + 1, 12)
                    ]
                ):
                    raise ValueError("yearly_schedule_mismatch")
            if benefit.lifetime_once and not any(
                rule.kind == "none_of" and rule.field == "domain_data.received"
                for rule in self.conditions
            ):
                raise ValueError("lifetime_policy_requires_received_rule")
        return self

    def published(self, published_at: datetime) -> PublishedPolicy:
        return PublishedPolicy(
            detail=self.benefit.model_dump(mode="json"),
            conditions=tuple(rule.model_dump(exclude_none=True) for rule in self.conditions),
            payments=None
            if self.payments is None
            else tuple((payment.month, payment.amount_won) for payment in self.payments),
            published_at=published_at.isoformat(),
        )


def record_hash(record: CatalogRecord) -> str:
    payload = record.model_dump(mode="json")
    # Existing fictional releases have no bindings; preserve their approved digest.
    if not record.collection_sources:
        payload.pop("collection_sources")
    encoded = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def validate_source_text(record: CatalogRecord, source_text: str) -> None:
    """Bind every quote and the approval to the exact locally retained source."""
    if hashlib.sha256(source_text.encode()).hexdigest() != record.source_sha256:
        raise ValueError("source_hash_mismatch")
    evidence = [e for c in record.benefit.eligibility_conditions for e in c.evidence]
    evidence += [e for c in record.benefit.conflicts for e in c.evidence]
    evidence += record.payment_evidence
    if record.benefit.application_guide is not None:
        evidence += record.benefit.application_guide.citations()
    if any(e.quote not in source_text for e in evidence):
        raise ValueError("quote_missing_from_source")
