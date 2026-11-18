"""Import amount-unknown policies and directly parsed cash components for the MVP."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import date

from benefitfinder.application.decider import Compilation
from benefitfinder.application.policy_analysis import PolicyAnalysisInput
from benefitfinder.application.region_options import RegionOption
from benefitfinder.ingestion.api_decider_update import compile_api_decider_update
from benefitfinder.ingestion.decider_cash_common import append_source_conditions
from benefitfinder.ingestion.decider_pending import _fields
from benefitfinder.ingestion.source_eligibility import compile_source_eligibility


def _versions(item, version):
    if isinstance(item, dict):
        return {
            key: version if key == "document_version" else _versions(child, version)
            for key, child in item.items()
        }
    if isinstance(item, list):
        return [_versions(child, version) for child in item]
    return item


def compile_mvp_cash_import(
    value: PolicyAnalysisInput,
    summary: dict,
    *,
    as_of: date,
    parsed: dict | None = None,
    artifact_ref: str | None = None,
    regions: Sequence[RegionOption] = (),
    source_missing_at: str | None = None,
) -> Compilation:
    """Keep unknown amounts null; map a parser's single claim to the existing runtime.

    No second model, model score, or individual approval is requested. The full
    parser result is retained even when no executable cash component is present.
    """
    base = compile_api_decider_update(value, summary, as_of=as_of)
    if base.payload is None:
        return base
    suffix = ":gpt6-parsed-v1" if parsed is not None else ":decider-unknown-v1"
    if source_missing_at is not None:
        suffix = ":mvp-missing-source-v1"
    version = "sha256:" + value.collection_sources[0].material_sha256 + suffix
    payload = _versions(base.payload, version)
    benefit = payload["benefit"]
    benefit["benefit_amount"].update(amount_status="unknown", amount_text="금액 확인불가")
    normalized = dict(base.normalized, status="amount_unknown", calculation_ready=False)
    if source_missing_at is not None:
        benefit["domain_data"]["source_presence"] = {
            "status": "missing",
            "detected_at": source_missing_at,
            "processing_status": "stored_for_database_review",
        }
    if parsed is None:
        return Compilation(payload, (), normalized)
    benefit["domain_data"]["gpt6_analysis"] = {
        "model": "gpt-6-astra",
        "requested_model": "gpt-6-luna",
        "question_version": "youth-cash-parse-v1",
        "artifact_ref": artifact_ref,
        "result": parsed,
        "ai_review_calls": 0,
    }
    claim = parsed.get("claim")
    if claim is None or source_missing_at is not None:
        return Compilation(payload, (), normalized)

    doc = value.documents[0]
    changed = replace(value, documents=(replace(doc, document_version=version),))
    conditions, rules = compile_source_eligibility(changed, regions=regions)
    append_source_conditions(payload, conditions, rules)
    fields = _fields(doc.text)
    source = benefit["source"]

    def evidence(field, quote):
        source_field = fields.get(field) or fields.get("plcySprtCn")
        if source_field is None or not source_field.text:
            return []
        index = source_field.text.find(quote) if isinstance(quote, str) and quote else -1
        # Full original field remains the citation if a model paraphrased its quote.
        text = quote if index >= 0 else source_field.text
        offset = source_field.start + max(index, 0)
        return [
            {
                "quote": text[start : start + 2400],
                "location": f"chars:{offset + start}-{offset + min(start + 2400, len(text))}",
                "source": source.copy(),
            }
            for start in range(0, len(text), 2400)
        ]

    citations = [
        citation
        for item in claim.get("evidence", [])
        for citation in evidence(item.get("field"), item.get("quote"))
    ] or evidence("plcySprtCn", None)
    label = (
        f"{claim['component_label']} · {claim['scope']} · "
        + (
            f"실제 비용의 {claim['reimbursement_bps'] / 100:g}%, 상한 {claim['amount_won']:,}원"
            if claim["mode"] == "expense"
            else f"{claim['amount_won']:,}원"
        )
        + "; 해당 구성요소만 계산, 지급예정월·개인 조건 확인 필요"
    )
    benefit["eligibility_conditions"].append(
        {"id": "mvp_parsed_claim", "description": label, "evidence": citations}
    )
    payload["conditions"].append(
        {
            "condition_id": "mvp_parsed_claim",
            "kind": "source_cash",
            "source_cash": {
                "policy_id": value.policy_id,
                "mode": claim["mode"],
                "amount_won": claim["amount_won"],
                "reimbursement_bps": claim["reimbursement_bps"],
            },
        }
    )
    for index, term in enumerate(claim.get("manual_terms", [])):
        term_id = f"mvp_parsed_term_{index}"
        benefit["eligibility_conditions"].append(
            {
                "id": term_id,
                "description": term["description"],
                "evidence": evidence(term.get("field"), term.get("quote")) or citations,
            }
        )
        payload["conditions"].append({"condition_id": term_id, "kind": "manual"})
    benefit.update(reviewed=True)
    benefit["benefit_amount"].update(
        type="lump_sum",
        amount_won=claim["amount_won"],
        period_months=1,
        max_total_won=claim["amount_won"],
        pay_timing="once",
        amount_text=label,
        amount_status="verified",
    )
    payload.update(payment_evidence=citations, unresolved_combination_rules=False)
    normalized.update(status="parsed_cash", calculation_ready=True)
    return Compilation(payload, (), normalized)
