"""Source-fact cash compilation for offline bulk publication, without AI review."""

from dataclasses import replace
from datetime import date
from collections.abc import Sequence
import re

from benefitfinder.application.decider import Compilation
from benefitfinder.application.policy_analysis import PolicyAnalysisInput
from benefitfinder.application.region_options import RegionOption
from benefitfinder.ingestion.decider_pending import _fields
from benefitfinder.ingestion.source_application import source_application
from benefitfinder.ingestion.source_cash_components import select_monthly_personal_component
from benefitfinder.ingestion.source_eligibility import compile_source_eligibility
from benefitfinder.ingestion.source_overlap import overlap_spans
from benefitfinder.ingestion.source_payment_facts import parse_source_payment
from benefitfinder.ingestion.youthcenter_codes import decode_zip_codes, YouthcenterCodeError


SOURCE_CASH_VERSION = "source-facts-v1"
SOURCE_CASH_DOCUMENT_SUFFIX = SOURCE_CASH_VERSION


def compile_source_cash(
    value: PolicyAnalysisInput, *, as_of: date, regions: Sequence[RegionOption] = (),
) -> Compilation:
    normalized = {"mode": SOURCE_CASH_VERSION, "model_calls": 0, "ai_review_calls": 0,
                  "calculation_ready": False}

    def hold(reason: str) -> Compilation:
        return Compilation(None, (reason,), normalized)

    if (len(value.documents) != 1 or len(value.collection_sources) != 1
            or value.collection_sources[0].source != "youthcenter"):
        return hold("source_cash_single_api_source_required")
    doc, binding = value.documents[0], value.collection_sources[0]
    if not doc.official or doc.policy_id != value.policy_id:
        return hold("source_cash_identity_mismatch")
    fields = _fields(doc.text)
    raw = {key: field.text.strip() for key, field in fields.items()}
    if raw.get("plcyNo") != binding.external_id:
        return hold("source_cash_identity_mismatch")
    target = next(policy for policy in value.policies if policy.id == value.policy_id)
    if target.name != (fields["plcyNm"].text if "plcyNm" in fields else None):
        return hold("source_cash_title_mismatch")
    labels = set(raw.get("lclsfNm", "").split(","))
    categories = [key for label, key in (("일자리", "employment"), ("주거", "housing"))
                  if label in labels]
    if not categories:
        return hold("source_cash_outside_mvp")
    if categories == ["employment"] and not set(raw.get("mclsfNm", "").split(",")) & {
        "취업", "재직자", "권익보호",
    }:
        return hold("source_cash_employment_scope_not_supported")
    title = target.name
    support = raw.get("plcySprtCn", "")
    component = select_monthly_personal_component(
        fields["plcySprtCn"].text,
        allow_first_year=bool(re.search(r"영농.*정착|어촌.*정착", title)),
    ) if "plcySprtCn" in fields else None
    if re.search(r"응시료|수강료|교육비|이사비|중개수수료|중개보수|주거비", title) and "정액" not in support:
        return hold("source_cash_actual_expense_requires_personal_cost")
    if raw.get("plcyPvsnMthdCd") in {"0042003", "0042007", "0042008", "0042009"}:
        return hold("source_cash_loan_tax_or_insurance")
    if re.search(r"대출|기숙사|임대주택|쉐어하우스|통장|저축|적금|내일채움공제|월세", title):
        return hold("source_cash_non_grant_or_savings")
    if (re.search(r"사업주|기업지원|채용기업|인건비\s*지원", title)
            or (component is None and (
                re.search(r"(?:기업|사업주|고용주)(?:에게|당|에\s*지급)", support)
                or re.search(r"(?:기업|업체)에\s*(?:군민|청년|신규|근로자).{0,60}(?:지급|지원)", support)))):
        return hold("source_cash_employer_recipient")
    if re.search(r"임대료\s*[:：]|기숙사비|이용비용|시급|일급|급여조건|세전", support):
        return hold("source_cash_cost_or_wage")
    if overlap_spans(doc.text):
        return hold("source_cash_overlap_requires_review")
    # The parsed payment text keeps complete, independently mapped field offsets.
    chunks = [(key, fields[key]) for key in (
        "plcySprtCn", "plcyAplyMthdCn", "srngMthdCn", "etcMttrCn",
    ) if key in fields]
    payment_text = "\n\n".join(field.text for _, field in chunks)
    parsed = parse_source_payment(payment_text)
    if parsed.amountfact is None and component is not None:
        selected = fields["plcySprtCn"]
        chunks = [("plcySprtCn", replace(selected,
            text=selected.text[component.start:component.end],
            start=selected.start + component.start))]
        payment_text = chunks[0][1].text
        parsed = parse_source_payment(payment_text)
        normalized["selected_component"] = component.label
    if parsed.amountfact is None:
        normalized["payment_reasons"] = list(parsed.reasons)
        return Compilation(None, tuple("source_cash_" + reason for reason in parsed.reasons), normalized)
    fact = parsed.amountfact
    if fact.pay_timing == "yearly":
        return hold("source_cash_annual_payment_calendar_not_stated")
    if not fact.payments:
        return hold("source_cash_payment_start_unknown_or_unsupported")
    if getattr(fact, "reasons", ()):
        normalized["payment_reasons"] = list(fact.reasons)
    doc = replace(doc, document_version="sha256:" + binding.material_sha256 + ":" + SOURCE_CASH_DOCUMENT_SUFFIX)
    value = replace(value, documents=(doc,))
    source = {"url": doc.source_url, "document_version": doc.document_version,
              "checked_at": doc.collected_at.isoformat()}
    conditions, rules = compile_source_eligibility(value, regions=regions)
    # A local notice with an unrepresentable region must not become nationwide.
    if raw.get("zipCd") and not any(rule.get("field") in {
        "region_code", "domain_data.region.sido_code",
    } for rule in rules):
        try:
            nationwide = decode_zip_codes(raw["zipCd"], regions).nationwide
        except (YouthcenterCodeError, TypeError, ValueError):
            nationwide = False
        if not nationwide:
            return hold("source_cash_residence_not_executable")
    offsets = []
    cursor = 0
    for _, field in chunks:
        offsets.append((cursor, cursor + len(field.text), field.start))
        cursor += len(field.text) + 2
    evidence = []
    for start, end in fact.evidence_spans:
        for lo, hi, original in offsets:
            left, right = max(lo, start), min(hi, end)
            if left < right:
                absolute = original + left - lo
                quote = doc.text[absolute:absolute + right - left]
                evidence.append({"quote": quote, "location": f"chars:{absolute}-{absolute + len(quote)}",
                                 "source": source.copy()})
    if not evidence:
        return hold("source_cash_payment_evidence_missing")
    if fact.lifetime_once:
        conditions.append({"id": "source_cash_no_previous", "description": "원문상 생애 1회 지급: 기존 수혜 여부 확인",
                           "evidence": evidence})
        rules.append({"condition_id": "source_cash_no_previous", "kind": "none_of",
                      "field": "domain_data.received", "values": [value.policy_id]})
    application, issues = source_application(raw, as_of=as_of)
    total = sum(amount for _, amount in fact.payments)
    amount_text = fact.amount_text.replace("actual_calendar_schedule_unknown", "실제 지급일은 공고 확인")
    amount_text = amount_text.replace("확정 ", "공고상 ")
    if "selected_component" in normalized:
        amount_text = "부분 계산(" + normalized["selected_component"] + "): " + amount_text
    payload = {
        "benefit": {"id": value.policy_id, "domain": "youth", "title": title,
                    "summary": amount_text + " / 공고의 추가 자격과 중복 수급 조건 확인 필요",
                    "provider": raw.get("sprvsnInstCdNm") or raw.get("rgtrInstCdNm") or "공식 공고 기관",
                    "categories": categories, "application": application,
                    "eligibility_conditions": conditions, "source": source,
                    "domain_data": {}, "benefit_amount": {
                        "type": fact.amount_type, "amount_won": fact.amount_won,
                        "period_months": fact.period_months, "max_total_won": total,
                        "pay_timing": fact.pay_timing, "amount_text": amount_text,
                        "amount_status": "verified",
                    }, "reviewed": True, "conflicts": [],
                    "lifetime_once": fact.lifetime_once, "virtual_data": False},
        "conditions": rules,
        "payments": [{"month": month, "amount_won": amount} for month, amount in fact.payments],
        "payment_evidence": evidence, "unresolved_combination_rules": False,
        "source_sha256": doc.content_sha256,
        "collection_sources": [{"source": binding.source, "external_id": binding.external_id,
                                "material_sha256": binding.material_sha256}],
    }
    normalized.update(calculation_ready=True, amount_won=fact.amount_won, total_won=total,
                      application_status=application["status"], application_issues=list(issues),
                      unknown_pairs="kept_as_conditional_by_existing_calculator")
    return Compilation(payload, (), normalized)


def compile_official_cash_component(
    value: PolicyAnalysisInput, *, component_span: tuple[int, int],
    application: dict, provider: str, component_description: str,
) -> Compilation:
    """Compile an explicitly selected cash component and retain the full notice.

    Selection identifies a component, never drops qualifications or exceptions:
    the complete primary document remains in manual evidence. Source identity,
    current material and API identity are rechecked by the publication writer.
    """
    normalized = {"mode": SOURCE_CASH_VERSION, "ai_review_calls": 0, "model_calls": 0,
                  "component_span": list(component_span), "calculation_ready": False}
    if (len(value.documents) != 1
            or not any(s.source == "official_documents" for s in value.collection_sources)):
        return Compilation(None, ("source_cash_official_primary_required",), normalized)
    doc = value.documents[0]
    start, end = component_span
    if not doc.official or not 0 <= start < end <= len(doc.text):
        return Compilation(None, ("source_cash_component_location_invalid",), normalized)
    if overlap_spans(doc.text):
        return Compilation(None, ("source_cash_overlap_requires_review",), normalized)
    parsed = parse_source_payment(doc.text[start:end])
    if parsed.amountfact is None:
        return Compilation(None, parsed.reasons, normalized)
    fact = parsed.amountfact
    if not fact.payments:
        return Compilation(None, ("source_cash_payment_start_unknown_or_unsupported",), normalized)
    source = {"url": doc.source_url, "document_version": doc.document_version,
              "checked_at": doc.collected_at.isoformat()}

    def evidence(lo: int, hi: int) -> dict:
        return {"quote": doc.text[lo:hi], "location": f"chars:{lo}-{hi}", "source": source.copy()}

    conditions, rules = [], []
    for index, lo in enumerate(range(0, len(doc.text), 2400)):
        condition_id = "official_source_terms_" + str(index)
        conditions.append({"id": condition_id,
                           "description": "공식 안내의 자격·제외·중복·소득 신고·지급 조건 확인: " + doc.text[lo:lo + 2400],
                           "evidence": [evidence(lo, min(lo + 2400, len(doc.text)))]})
        rules.append({"condition_id": condition_id, "kind": "manual"})
    conditions.append({"id": "calculated_component", "description": component_description,
                       "evidence": [evidence(start, end)]})
    rules.append({"condition_id": "calculated_component", "kind": "manual"})
    if fact.lifetime_once:
        conditions.append({"id": "source_cash_no_previous", "description": "원문의 생애 1회 수급 조건 확인",
                           "evidence": [evidence(start, end)]})
        rules.append({"condition_id": "source_cash_no_previous", "kind": "none_of",
                      "field": "domain_data.received", "values": [value.policy_id]})
    target = next(p for p in value.policies if p.id == value.policy_id)
    total = sum(amount for _, amount in fact.payments)
    amount_text = fact.amount_text.replace("actual_calendar_schedule_unknown", "실제 지급일은 공고 확인")
    amount_text = amount_text.replace("확정 ", "공고상 ") + " / " + component_description
    payload = {
        "benefit": {"id": value.policy_id, "domain": "youth", "title": target.name,
                    "summary": amount_text, "provider": provider, "categories": list(target.categories),
                    "application": application, "eligibility_conditions": conditions,
                    "source": source, "domain_data": {}, "benefit_amount": {
                        "type": fact.amount_type, "amount_won": fact.amount_won,
                        "period_months": fact.period_months, "max_total_won": total,
                        "pay_timing": fact.pay_timing, "amount_text": amount_text, "amount_status": "verified",
                    }, "reviewed": True, "conflicts": [], "lifetime_once": fact.lifetime_once,
                    "virtual_data": False},
        "conditions": rules, "payments": [{"month": m, "amount_won": a} for m, a in fact.payments],
        "payment_evidence": [evidence(start + lo, start + hi) for lo, hi in fact.evidence_spans],
        "unresolved_combination_rules": False, "source_sha256": doc.content_sha256,
        "collection_sources": [{"source": s.source, "external_id": s.external_id,
                                "material_sha256": s.material_sha256} for s in value.collection_sources],
    }
    normalized.update(calculation_ready=True, total_won=total,
                      full_notice_preserved_in_manual_terms=True)
    return Compilation(payload, (), normalized)
