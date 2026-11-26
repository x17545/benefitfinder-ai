"""Normalize explicit expense clauses selected by the offline Decider."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import date
import re

from benefitfinder.application.decider import Compilation
from benefitfinder.application.policy_analysis import PolicyAnalysisInput
from benefitfinder.application.region_options import RegionOption
from benefitfinder.ingestion.api_decider_update import compile_api_decider_update
from benefitfinder.ingestion.decider_pending import _fields
from benefitfinder.ingestion.decider_cash_common import reimbursement_rates
from benefitfinder.ingestion.source_application import source_application
from benefitfinder.ingestion.source_eligibility import compile_source_eligibility
from benefitfinder.ingestion.source_overlap import overlap_spans
from benefitfinder.ingestion.source_payment_facts import _MONEY_RE, _parse_money
from benefitfinder.ingestion.youthcenter_codes import decode_zip_codes


_EXPENSE = r"응시료|보증료|이사비|중개\s*(?:수수료|보수)|검정료"
_CAP = re.compile(r"최대|한도|상한|이내|범위")


def compile_decider_expense_cash(
    value: PolicyAnalysisInput,
    summary: dict,
    *,
    as_of: date,
    regions: Sequence[RegionOption] = (),
) -> Compilation:
    """Keep an actual cost, explicit reimbursement rate and one source cap.

    Decisions identify clause meaning. Code reads numbers and percentages from
    the unchanged source; an applicant still supplies cost and payment month.
    No quality score, second model review or individual approval is used.
    """
    base = compile_api_decider_update(value, summary, as_of=as_of)
    normalized = dict(base.normalized, mode="decider-expense-cash-v1")

    def hold(reason: str) -> Compilation:
        return Compilation(None, (reason,), normalized)

    if base.payload is None:
        return base
    answers = summary["decisions"]
    if not any(item.get("amount_kind", {}).get("choice") == "cap" for item in answers):
        return hold("decider_expense_not_a_cap")
    recipients = {item.get("recipient", {}).get("choice") for item in answers}
    if "applicant" not in recipients or recipients & {"employer", "mixed"}:
        return hold("decider_expense_not_personal")
    document, binding = value.documents[0], value.collection_sources[0]
    fields = _fields(document.text)
    raw = {key: field.text.strip() for key, field in fields.items()}
    title = next(policy.name for policy in value.policies if policy.id == value.policy_id)
    support = fields.get("plcySprtCn")
    if support is None or not re.search(_EXPENSE, title) or not re.search(_EXPENSE, support.text):
        return hold("decider_expense_explicit_expense_required")
    if raw.get("plcyPvsnMthdCd") in {"0042003", "0042007", "0042008", "0042009"}:
        return hold("decider_expense_non_cash_method")
    if overlap_spans(document.text):
        return hold("decider_expense_overlap_requires_mapping")
    candidates = {}
    for match in _MONEY_RE.finditer(support.text):
        left = support.text.rfind("\n", 0, match.start()) + 1
        right = support.text.find("\n", match.end())
        line = support.text[left : right if right >= 0 else len(support.text)]
        if re.search(r"소득|재산|총\s*사업비|사업\s*예산|임차\s*보증금", line):
            continue
        if not _CAP.search(line):
            continue
        amount = _parse_money(match.group())
        if amount is None:
            return hold("decider_expense_amount_invalid")
        absolute = support.start + match.start()
        if not any(
            span["kind"] == "amount"
            and span["start"] < absolute + len(match.group())
            and absolute < span["end"]
            for span in summary["evidence"]
        ):
            continue
        candidates.setdefault(amount, []).append((absolute, absolute + len(match.group())))
    if len(candidates) != 1:
        return hold("decider_expense_single_located_cap_required")
    amount, positions = next(iter(candidates.items()))
    try:
        rates_bps = reimbursement_rates(support.text, _EXPENSE)
    except ValueError:
        return hold("decider_expense_reimbursement_rate_ambiguous")
    rates = {value / 100 for value in rates_bps}
    if rates:
        if len(rates) != 1 or not 0 < next(iter(rates)) <= 100:
            return hold("decider_expense_reimbursement_rate_ambiguous")
        bps = next(iter(rates_bps))
    elif re.search(r"실비|전액|100\s*%", support.text):
        bps = 10000
    else:
        return hold("decider_expense_reimbursement_rate_missing")
    version = "sha256:" + binding.material_sha256 + ":decider-expense-v2"
    source = dict(base.payload["benefit"]["source"], document_version=version)

    def versions(item):
        if isinstance(item, dict):
            return {
                key: (version if key == "document_version" else versions(child))
                for key, child in item.items()
            }
        if isinstance(item, list):
            return [versions(child) for child in item]
        return item

    payload = versions(base.payload)
    application, application_issues = source_application(raw, as_of=as_of)
    payload["benefit"]["application"] = application
    normalized["application_issues"] = list(application_issues)
    changed = replace(value, documents=(replace(document, document_version=version),))
    conditions, rules = compile_source_eligibility(changed, regions=regions)
    if raw.get("zipCd") and not any(
        rule.get("field")
        in {
            "region_code",
            "domain_data.region.sido_code",
        }
        for rule in rules
    ):
        try:
            if not decode_zip_codes(raw["zipCd"], regions).nationwide:
                return hold("decider_expense_residence_not_executable")
        except ValueError:
            return hold("decider_expense_residence_not_executable")
    payload["benefit"]["eligibility_conditions"].extend(conditions)
    payload["conditions"].extend(rules)
    label = (
        f"1건 실비의 {bps / 100:g}% 지원, 상한 {amount:,}원; 실제 지출액·지급예정월 입력 시 계산"
    )
    evidence = [
        {
            "quote": document.text[start:end],
            "location": f"chars:{start}-{end}",
            "source": source.copy(),
        }
        for start, end in positions
    ]
    terms = [
        {
            "quote": support.text[start : start + 2400],
            "location": f"chars:{support.start + start}-{support.start + min(start + 2400, len(support.text))}",
            "source": source.copy(),
        }
        for start in range(0, len(support.text), 2400)
    ]
    payload["benefit"]["eligibility_conditions"].append(
        {
            "id": "decider_expense_claim",
            "description": label,
            "evidence": terms,
        }
    )
    payload["conditions"].append(
        {
            "condition_id": "decider_expense_claim",
            "kind": "source_cash",
            "source_cash": {
                "policy_id": value.policy_id,
                "mode": "expense",
                "amount_won": amount,
                "reimbursement_bps": bps,
            },
        }
    )
    lifetime = bool(re.search(r"생애\s*(?:1\s*회|한\s*번)|평생\s*1\s*회", document.text))
    if lifetime:
        payload["benefit"]["eligibility_conditions"].append(
            {
                "id": "decider_expense_previous",
                "description": "공고상 생애 1회 지원",
                "evidence": terms,
            }
        )
        payload["conditions"].append(
            {
                "condition_id": "decider_expense_previous",
                "kind": "none_of",
                "field": "domain_data.received",
                "values": [value.policy_id],
            }
        )
    payload["benefit"].update(
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
    payload.update(payments=None, payment_evidence=evidence, unresolved_combination_rules=False)
    normalized.update(
        calculation_ready=True,
        reimbursement_bps=bps,
        amount_won=amount,
        claim_count=1,
        stored_payment_schedule=None,
    )
    return Compilation(payload, (), normalized)
