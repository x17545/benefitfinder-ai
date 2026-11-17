"""Compile one literal reimbursable cost component without inventing a payout."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import date
import re

from benefitfinder.application.decider import Compilation
from benefitfinder.application.policy_analysis import PolicyAnalysisInput
from benefitfinder.application.region_options import RegionOption
from benefitfinder.ingestion.api_decider_update import compile_api_decider_update
from benefitfinder.ingestion.decider_cash_common import (
    append_source_conditions,
    reimbursement_rates,
)
from benefitfinder.ingestion.decider_pending import _fields
from benefitfinder.ingestion.source_application import source_application
from benefitfinder.ingestion.source_eligibility import compile_source_eligibility
from benefitfinder.ingestion.source_overlap import overlap_spans
from benefitfinder.ingestion.source_payment_facts import _MONEY_RE, _parse_money
from benefitfinder.ingestion.youthcenter_codes import decode_zip_codes

_COST = re.compile(
    r"응시료|검정료|이사(?:비|비용)|중개\s*(?:보수|수수료|비)|보증료|월세|임차료|월\s*임대료|"
    r"대출\s*이자|이자\s*납부액|전세\s*대출이자"
)
_CAP_BEFORE = re.compile(r"(?:최대|한도|상한|까지|범위)\s*(?:연간?|월|인당)?\s*$")
_CAP_AFTER = re.compile(r"^\s*(?:까지|한도|이내|이하|범위|상한)")
_MONTHLY = re.compile(r"월\s*(?:최대|상한|한도)?\s*$|매월|매달")
_REIMBURSE = re.compile(
    r"실비|전액|실\s*납부|기\s*납부|납부한|납부액|실제\s*(?:부담|납부|지출|결제)|"
    r"납부하는|지출한|지출액|해당\s*금액만큼|금액\s*범위\s*(?:내|이내)"
)
_NON_GRANT = re.compile(r"융자|대출\s*한도|대출\s*금리|담보별|대출금\s*지원|임대주택\s*공급")
_PRINCIPAL_RATE = re.compile(r"(?:잔액|대출금|원금|대출금액|대출금\(잔\)액)[^\n]{0,20}%")


def compile_decider_reimbursement_cash(
    value: PolicyAnalysisInput,
    summary: dict,
    *,
    as_of: date,
    regions: Sequence[RegionOption] = (),
) -> Compilation:
    """One monthly/annual/individual expense component needs actual applicant facts.

    Full source and native outputs remain available. The code determines literal
    cost percentages and caps, never treats the cap as a promised payment, and
    never multiplies an advertised maximum duration or claim count.
    """
    base = compile_api_decider_update(value, summary, as_of=as_of)
    normalized = dict(base.normalized, mode="decider-reimbursement-v1")

    def hold(reason: str) -> Compilation:
        return Compilation(None, (reason,), normalized)

    if base.payload is None:
        return base
    doc, binding = value.documents[0], value.collection_sources[0]
    fields = _fields(doc.text)
    raw = {key: field.text.strip() for key, field in fields.items()}
    support = fields.get("plcySprtCn")
    if support is None or not _COST.search(base.payload["benefit"]["title"]):
        return hold("decider_reimbursement_cost_policy_required")
    text = support.text
    if raw.get("plcyPvsnMthdCd") in {"0042003", "0042008", "0042009"} or _NON_GRANT.search(text):
        return hold("decider_reimbursement_not_cash_grant")
    if _PRINCIPAL_RATE.search(text):
        return hold("decider_reimbursement_principal_basis_not_cost")
    if overlap_spans(doc.text):
        return hold("decider_reimbursement_overlap_requires_mapping")
    # Keep alternatives, household tiers and old/new caps separate until mapped.
    if re.search(
        r"전부\s*또는\s*일부|차등|가구\s*별|1인\s*가구[^\n]{0,60}2인|이전[^\n]{0,25}최대", text
    ):
        return hold("decider_reimbursement_alternative_amounts")
    candidates = []
    for match in _MONEY_RE.finditer(text):
        before = text[max(0, match.start() - 35) : match.start()]
        after = text[match.end() : match.end() + 18]
        if not (_CAP_BEFORE.search(before) or _CAP_AFTER.search(after)):
            continue
        if re.search(
            r"(?:소득|재산|예산|총\s*사업비|보증금|전세가격|주택가격|거래금액|거래가액)[^\n]{0,15}$",
            before,
        ):
            continue
        amount = _parse_money(match.group())
        if amount is None:
            return hold("decider_reimbursement_amount_invalid")
        # A monthly cap is distinct from a full programme's total cap.
        monthly = bool(_MONTHLY.search(before))
        total = bool(re.search(r"총\s*(?:최대)?\s*$", before))
        candidates.append((amount, match.start(), match.end(), monthly, total))
    monthly = [item for item in candidates if item[3]]
    if len({item[0] for item in candidates if not item[4]}) > 1 and re.search(
        r"또는|혹은|중\s*선택", text
    ):
        return hold("decider_reimbursement_alternative_amounts")
    selected = monthly or [item for item in candidates if not item[4]]
    amounts = {item[0] for item in selected}
    if len(amounts) != 1:
        return hold("decider_reimbursement_single_component_cap_required")
    amount = next(iter(amounts))
    try:
        rates = reimbursement_rates(text, _COST.pattern)
    except ValueError:
        return hold("decider_reimbursement_invalid_percentage")
    if len(rates) > 1:
        return hold("decider_reimbursement_ambiguous_percentage")
    if rates:
        bps = next(iter(rates))
    else:
        supplemental = "\n".join(
            raw.get(key, "") for key in ("etcMttrCn", "addAplyQlfcCndCn", "plcyAplyMthdCn")
        )
        if not _REIMBURSE.search(text) and not (
            _COST.search(supplemental) and _REIMBURSE.search(supplemental)
        ):
            return hold("decider_reimbursement_rate_not_stated")
        bps = 10000
    version = "sha256:" + binding.material_sha256 + ":decider-reimburse-v1"

    def versions(item):
        if isinstance(item, dict):
            return {
                key: version if key == "document_version" else versions(child)
                for key, child in item.items()
            }
        if isinstance(item, list):
            return [versions(child) for child in item]
        return item

    payload = versions(base.payload)
    source = payload["benefit"]["source"]
    changed = replace(value, documents=(replace(doc, document_version=version),))
    conditions, rules = compile_source_eligibility(changed, regions=regions)
    if raw.get("zipCd") and not any(
        rule.get("field") in {"region_code", "domain_data.region.sido_code"} for rule in rules
    ):
        try:
            if not decode_zip_codes(raw["zipCd"], regions).nationwide:
                return hold("decider_reimbursement_region_not_executable")
        except ValueError:
            return hold("decider_reimbursement_region_not_executable")
    append_source_conditions(payload, conditions, rules)
    unit = "월 비용 1개월분" if monthly else "적격 비용 1건분"
    label = f"{unit}의 {bps / 100:g}% 실비, 상한 {amount:,}원; 실제 비용·지급예정월 입력 시 계산, 전체 기간·횟수 미합산"
    terms = [
        {
            "quote": text[start : start + 2400],
            "location": f"chars:{support.start + start}-{support.start + min(start + 2400, len(text))}",
            "source": source.copy(),
        }
        for start in range(0, len(text), 2400)
    ]
    payload["benefit"]["eligibility_conditions"].append(
        {"id": "decider_cost_claim", "description": label, "evidence": terms}
    )
    payload["conditions"].append(
        {
            "condition_id": "decider_cost_claim",
            "kind": "source_cash",
            "source_cash": {
                "policy_id": value.policy_id,
                "mode": "expense",
                "amount_won": amount,
                "reimbursement_bps": bps,
            },
        }
    )
    lifetime = bool(re.search(r"생애\s*(?:1\s*회|한\s*번)|평생\s*1\s*회", doc.text))
    if lifetime:
        payload["benefit"]["eligibility_conditions"].append(
            {
                "id": "decider_cost_previous",
                "description": "공고상 생애 1회 지원",
                "evidence": terms,
            }
        )
        payload["conditions"].append(
            {
                "condition_id": "decider_cost_previous",
                "kind": "none_of",
                "field": "domain_data.received",
                "values": [value.policy_id],
            }
        )
    payload["benefit"]["application"], issues = source_application(raw, as_of=as_of)
    payload["benefit"].update(
        summary=label,
        reviewed=True,
        lifetime_once=True if lifetime else None,
        benefit_amount={
            "type": "lump_sum",
            "amount_won": amount,
            "period_months": 1,
            "max_total_won": amount,
            "pay_timing": "once",
            "amount_text": label,
            "amount_status": "verified",
        },
    )
    evidence = [
        {
            "quote": text[lo:hi],
            "location": f"chars:{support.start + lo}-{support.start + hi}",
            "source": source.copy(),
        }
        for _, lo, hi, _, _ in selected
    ]
    payload.update(payments=None, payment_evidence=evidence, unresolved_combination_rules=False)
    normalized.update(
        calculation_ready=True,
        cap_won=amount,
        reimbursement_bps=bps,
        component=unit,
        application_issues=list(issues),
        stored_payment_schedule=None,
    )
    return Compilation(payload, (), normalized)
