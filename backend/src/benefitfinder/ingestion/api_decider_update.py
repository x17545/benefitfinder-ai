"""Publish API fields with offline Decider annotations, without an AI review step."""

from datetime import date
import hashlib
import json
from typing import Any

from benefitfinder.application.decider import Compilation, MODEL_ID, MODEL_REVISION
from benefitfinder.application.policy_analysis import PolicyAnalysisInput
from benefitfinder.ingestion.decider_pending import _application, _fields, _manual_conditions


QUESTION_VERSION = "api-first-locate-decide-v2"
_KINDS = {"amount", "period", "eligibility", "application", "overlap", "exceptions"}


def decider_update_summary(artifact: dict[str, Any], *, artifact_ref: str) -> dict[str, Any]:
    """Keep actual outputs and source coordinates; scores never authorize publication."""
    model = artifact["model"]
    if (
        artifact.get("question_version") != QUESTION_VERSION
        or model.get("id") != MODEL_ID
        or model.get("revision") != MODEL_REVISION
        or model.get("provider") != "local"
        or model.get("fast") != "false"
    ):
        raise ValueError("api_decider_model_identity_mismatch")
    text = artifact["source_projection"]
    if hashlib.sha256(text.encode()).hexdigest() != artifact["source_sha256"]:
        raise ValueError("api_decider_source_hash_mismatch")
    evidence = []
    for span in artifact["extraction_spans"]:
        start, end = span["start"], span["end"]
        if (
            span["kind"] not in _KINDS
            or type(start) is not int
            or type(end) is not int
            or not 0 <= start < end <= len(text)
            or text[start:end] != span["text"]
        ):
            raise ValueError("api_decider_evidence_mismatch")
        evidence.append(
            {key: span[key] for key in ("kind", "field", "start", "end", "text", "score")}
        )
    summary = {
        "format_version": 1,
        "mode": "api_first_model_update",
        "model": model,
        "question_version": QUESTION_VERSION,
        "artifact_ref": artifact_ref,
        "source_sha256": artifact["source_sha256"],
        "material_sha256": artifact["material_sha256"],
        "evidence": evidence,
        "decisions": [item["answers"] for item in artifact["decisions"]],
        "analysis_complete": True,
        "ai_review_calls": 0,
        "calculation_ready": False,
    }
    if len(json.dumps(summary, ensure_ascii=False).encode()) > 60_000:
        # Full quotes remain in the immutable private artifact and original API
        # projection. Store every coordinate, rather than discarding long spans.
        summary["evidence"] = [
            {key: value for key, value in item.items() if key != "text"} for item in evidence
        ]
        summary["evidence_text_storage"] = "artifact_and_original_source"
    if len(json.dumps(summary, ensure_ascii=False).encode()) > 65_000:
        raise ValueError("api_decider_result_summary_too_large")
    return summary


def compile_api_decider_update(
    value: PolicyAnalysisInput,
    summary: dict[str, Any],
    *,
    as_of: date,
) -> Compilation:
    """Prepare a source-bound catalogue update even when cash amount is absent.

    API and model outputs are retained as facts and annotations. Missing payment
    facts remain unknown; the existing calculators continue to own all amounts.
    """
    normalized = {
        "mode": "api_first_model_update",
        "analysis_complete": True,
        "calculation_ready": False,
        "ai_review_calls": 0,
    }

    def hold(reason: str) -> Compilation:
        return Compilation(None, (reason,), normalized)

    if (
        len(value.documents) != 1
        or len(value.collection_sources) != 1
        or value.collection_sources[0].source != "youthcenter"
    ):
        return hold("api_decider_single_api_source_required")
    document, binding = value.documents[0], value.collection_sources[0]
    if (
        summary.get("source_sha256") != document.content_sha256
        or summary.get("material_sha256") != binding.material_sha256
    ):
        return hold("api_decider_source_mismatch")
    fields = _fields(document.text)
    raw = {key: field.text.strip() for key, field in fields.items()}
    target = next(policy for policy in value.policies if policy.id == value.policy_id)
    if (
        raw.get("plcyNo") != binding.external_id
        or "plcyNm" not in fields
        or fields["plcyNm"].text.strip() != target.name
    ):
        return hold("api_decider_policy_identity_mismatch")
    labels = set(raw.get("lclsfNm", "").split(","))
    if not labels or not labels <= {"일자리", "주거"}:
        return hold("api_decider_catalog_outside_mvp")
    if "일자리" in labels and not set(raw.get("mclsfNm", "").split(",")) <= {
        "취업",
        "재직자",
        "권익보호",
    }:
        return hold("api_decider_catalog_outside_mvp")
    provider = raw.get("rgtrInstCdNm") or raw.get("sprvsnInstCdNm")
    if not provider:
        return hold("api_decider_provider_missing")
    source = {
        "url": document.source_url,
        "document_version": "sha256:" + binding.material_sha256 + ":decider-v3",
        "checked_at": document.collected_at.isoformat(),
    }
    conditions, rules = _manual_conditions(fields, source)
    application, application_reason = _application(fields, as_of)
    support = raw.get("plcySprtCn") or raw.get("plcyExplnCn") or target.name
    payload = {
        "benefit": {
            "id": value.policy_id,
            "domain": "youth",
            "title": target.name,
            "summary": support,
            "provider": provider,
            "categories": [
                key
                for label, key in (("일자리", "employment"), ("주거", "housing"))
                if label in labels
            ],
            "application": application,
            "eligibility_conditions": conditions,
            "source": source,
            "domain_data": {"decider_analysis": summary},
            "benefit_amount": {
                "type": None,
                "amount_won": None,
                "period_months": None,
                "max_total_won": None,
                "pay_timing": None,
                "amount_text": support,
                "amount_status": "unknown",
            },
            "reviewed": False,
            "conflicts": [],
            "lifetime_once": None,
            "virtual_data": False,
        },
        "conditions": rules,
        "payments": None,
        "payment_evidence": [],
        "unresolved_combination_rules": True,
        "source_sha256": document.content_sha256,
        "collection_sources": [
            {
                "source": binding.source,
                "external_id": binding.external_id,
                "material_sha256": binding.material_sha256,
            }
        ],
    }
    normalized.update(status="model_updated", application_reason=application_reason)
    return Compilation(payload, (), normalized)
