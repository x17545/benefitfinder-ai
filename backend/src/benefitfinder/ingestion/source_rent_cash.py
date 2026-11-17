"""Deterministic source compilation for the supported dynamic rent-support policy.

The existing strict rent-support candidate supplies the executable household and
deduction rule. This module only joins it to verified API scalars, the injected
region catalogue and current recruitment dates. The stored cap stays a cap; the
existing applicant-dependent calculator resolves the actual monthly payments.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import date

from benefitfinder.application.decider import Compilation
from benefitfinder.application.policy_analysis import PolicyAnalysisInput
from benefitfinder.application.region_options import RegionOption
from benefitfinder.ingestion.rent_support import rent_support_candidate, rent_support_record
from benefitfinder.ingestion.source_application import source_application
from benefitfinder.ingestion.source_eligibility import compile_source_eligibility
from benefitfinder.ingestion.source_overlap import overlap_spans
from benefitfinder.ingestion.youthcenter_codes import YouthcenterCodeError, decode_zip_codes


SOURCE_RENT_CASH_VERSION = "source-rent-facts-v1"
# Reuse the source-bound publication contract. The compiler revision remains in
# normalized audit metadata; it does not invent another DB document identity.
SOURCE_RENT_CASH_DOCUMENT_SUFFIX = "source-facts-v1"

_EVIDENCE_CHUNK = 2400
_REGION_FIELDS = frozenset({"region_code", "domain_data.region.sido_code"})
_REQUIRED_CODE_FIELDS = ("jobCd", "schoolCd", "mrgSttsCd")


def _evidence(doc, start: int, end: int, source: dict[str, str]) -> dict:
    return {
        "quote": doc.text[start:end],
        "location": f"chars:{start}-{end}",
        "source": source.copy(),
    }


def compile_source_rent_cash(
    value: PolicyAnalysisInput,
    *,
    as_of: date,
    regions: Sequence[RegionOption] = (),
) -> Compilation:
    """Compile one verified rent-support API projection without model calls.

    The strict candidate in ingestion.rent_support remains the source of truth.
    Unsupported source forms hold instead of being generalized, and a possible
    overlap restriction holds until another compiler can resolve it.
    """
    normalized = {
        "mode": SOURCE_RENT_CASH_VERSION,
        "model_calls": 0,
        "ai_review_calls": 0,
        "calculation_ready": False,
        "payments": None,
    }

    def hold(reason: str) -> Compilation:
        return Compilation(None, (reason,), normalized)

    if (
        len(value.documents) != 1
        or len(value.collection_sources) != 1
        or value.collection_sources[0].source != "youthcenter"
    ):
        return hold("source_rent_cash_single_api_source_required")

    original_doc = value.documents[0]
    binding = value.collection_sources[0]
    if not original_doc.official or original_doc.policy_id != value.policy_id:
        return hold("source_rent_cash_identity_mismatch")
    if overlap_spans(original_doc.text):
        return hold("source_rent_cash_overlap_requires_review")

    candidate = rent_support_candidate(value, ())
    if candidate is None:
        return hold("source_rent_cash_supported_terms_required")

    target = next(policy for policy in value.policies if policy.id == value.policy_id)
    fields = candidate["source_fields"]
    if fields.get("plcyNo") != binding.external_id:
        return hold("source_rent_cash_identity_mismatch")
    if fields.get("plcyNm") != target.name:
        return hold("source_rent_cash_title_mismatch")

    review_updates = [
        item.get("field")
        for item in candidate["field_updates"]
        if item.get("status") == "needs_review"
    ]
    if review_updates:
        normalized["review_required_fields"] = review_updates
        return hold("source_rent_cash_source_change_requires_review")

    try:
        application, application_issues = source_application(fields, as_of=as_of)
    except (TypeError, ValueError):
        return hold("source_rent_cash_application_dates_invalid")
    if application_issues or application["status"] == "unknown":
        normalized["application_issues"] = list(application_issues)
        return hold("source_rent_cash_application_period_required")

    doc = replace(
        original_doc,
        document_version=f"sha256:{binding.material_sha256}:{SOURCE_RENT_CASH_DOCUMENT_SUFFIX}",
    )
    value = replace(value, documents=(doc,))
    try:
        record = rent_support_record(value, candidate)
    except (KeyError, TypeError, ValueError) as error:
        reason = str(error)
        if reason == "rent_application_dates_unknown":
            return hold("source_rent_cash_application_period_required")
        if reason == "rent_source_region_unknown":
            return hold("source_rent_cash_region_not_executable")
        return hold("source_rent_cash_supported_record_required")

    try:
        source_conditions, source_rules = compile_source_eligibility(value, regions=regions)
    except (TypeError, ValueError):
        return hold("source_rent_cash_eligibility_compile_failed")

    source_pairs = list(zip(source_conditions, source_rules, strict=True))
    manual_ids = {condition["id"] for condition, rule in source_pairs if rule["kind"] == "manual"}
    if any(
        str(fields.get(name, "")).strip() and f"source:{name}" in manual_ids
        for name in _REQUIRED_CODE_FIELDS
    ):
        return hold("source_rent_cash_structured_field_not_executable")

    source_age = next((rule for rule in source_rules if rule.get("condition_id") == "age"), None)
    candidate_age = next(
        (rule for rule in candidate["conditions"] if rule.get("condition_id") == "age"), None
    )
    if source_age is not None and candidate_age is not None:
        age_fields = ("minimum", "maximum", "military_extension_years")
        if any(source_age.get(name) != candidate_age.get(name) for name in age_fields):
            return hold("source_rent_cash_eligibility_conflict")
    if fields.get("sprtTrgtAgeLmtYn") == "Y" and candidate_age is not None:
        return hold("source_rent_cash_eligibility_conflict")

    region_rules = [
        rule
        for rule in source_rules
        if rule.get("kind") == "selection" and rule.get("field") in _REGION_FIELDS
    ]
    raw_zip = str(fields.get("zipCd", "")).strip()
    if raw_zip and not region_rules:
        try:
            nationwide = decode_zip_codes(raw_zip, regions).nationwide
        except (TypeError, ValueError, YouthcenterCodeError):
            nationwide = False
        if not nationwide:
            return hold("source_rent_cash_region_not_executable")

    conditions = []
    rules = []
    for condition, rule in zip(
        record["benefit"]["eligibility_conditions"], record["conditions"], strict=True
    ):
        # raw zipCd is replaced by the decoded catalogue rule below when present.
        if rule.get("condition_id") == "residence":
            continue
        conditions.append(dict(condition))
        rules.append(dict(rule))

    existing = {rule["condition_id"]: rule for rule in rules}
    for condition, rule in source_pairs:
        if rule["kind"] == "manual" or rule.get("condition_id") == "age":
            continue
        condition_id = rule["condition_id"]
        prior = existing.get(condition_id)
        if prior is not None:
            if prior != rule:
                return hold("source_rent_cash_eligibility_conflict")
            continue
        conditions.append(dict(condition))
        rules.append(dict(rule))
        existing[condition_id] = rule

    source = {
        "url": doc.source_url,
        "document_version": doc.document_version,
        "checked_at": doc.collected_at.isoformat(),
    }
    manual_terms = [
        _evidence(doc, start, min(start + _EVIDENCE_CHUNK, len(doc.text)), source)
        for start in range(0, len(doc.text), _EVIDENCE_CHUNK)
    ]
    rent_condition = next(
        condition
        for condition, rule in zip(conditions, rules, strict=True)
        if rule["kind"] == "rent_support"
    )
    rent_condition["evidence"] = manual_terms

    record["benefit"]["application"] = application
    record["benefit"]["eligibility_conditions"] = conditions
    record["benefit"]["reviewed"] = True
    record["conditions"] = rules
    record["payments"] = None
    record["payment_evidence"] = manual_terms
    record["unresolved_combination_rules"] = False

    rent_rule = next(rule["rent_support"] for rule in rules if rule["kind"] == "rent_support")
    normalized.update(
        status="compiled",
        calculation_ready=True,
        amount_won=rent_rule["monthly_cap_won"],
        period_months=rent_rule["maximum_months"],
        max_total_won=rent_rule["monthly_cap_won"] * rent_rule["maximum_months"],
        application_status=application["status"],
        api_condition_ids=[
            rule["condition_id"]
            for condition, rule in source_pairs
            if rule["kind"] != "manual" and rule["condition_id"] != "age"
        ],
        full_notice_preserved_in_manual_terms=True,
        manual_terms_condition="rent_support",
        source_sha256=doc.content_sha256,
    )
    return Compilation(record, (), normalized)


__all__ = [
    "SOURCE_RENT_CASH_DOCUMENT_SUFFIX",
    "SOURCE_RENT_CASH_VERSION",
    "compile_source_rent_cash",
]
