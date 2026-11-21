"""Compile one literal, applicant-specific fixed cash component from API facts.

Native decisions are provenance only. The deterministic API payment parser owns
the amount, while the existing source-cash runtime owns the applicant month and
exclusion facts. A multi-component notice contributes at most one payment.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date
import re

from benefitfinder.application.decider import Compilation
from benefitfinder.application.policy_analysis import PolicyAnalysisInput
from benefitfinder.application.region_options import RegionOption
from benefitfinder.ingestion.api_decider_update import compile_api_decider_update
from benefitfinder.ingestion.decider_cash_common import append_source_conditions
from benefitfinder.ingestion.decider_pending import _fields
from benefitfinder.ingestion.source_application import source_application
from benefitfinder.ingestion.source_eligibility import compile_source_eligibility
from benefitfinder.ingestion.source_overlap import overlap_spans
from benefitfinder.ingestion.source_payment_facts import (
    _MONEY_RE,
    _Money,
    _amount_rejection,
    _annual_marker,
    _has_support_anchor,
    _monthly_marker,
    _parse_money,
    parse_source_payment,
)
from benefitfinder.ingestion.youthcenter_codes import YouthcenterCodeError, decode_zip_codes


DOCUMENT_VERSION_SUFFIX = ":decider-fixed-v1"
_MAX_EVIDENCE = 2400
_BOUNDARY = re.compile(r"\r?\n|[□○ㅇ▪◈❍◎]|(?<!\d)[.!?](?!\d)\s+")
_CAP = re.compile(r"최대|최장|한도|상한|이내|이하|범위|차등|구간별|\d\s*[~∼～]\s*\d")
_NON_GRANT = re.compile(
    r"대출|융자|보증금\s*지원|임대주택|기숙사|통장|적금|저축|대여|인건비|급여|임금|시급|세전|"
    r"개\s*(?:소|사)\s*당|사업주|인턴|고용장려금|수강권|이용권|수강료|기업\s*(?:지원|인증|선정)|우수기업"
)
_ACTUAL_COST = re.compile(r"응시료|수강료|교육비|이사비|중개수수료|중개보수|주거비|보증료")
_LIFETIME = re.compile(r"생애\s*(?:1\s*회|한\s*번)|평생\s*1\s*회")
_PERSONAL_RECIPIENT = re.compile(r"\(\s*청년\s*\)|청년\s*[:：]|청년\s*(?:에게|대상)")
_IGNORABLE_OTHER_MONEY = re.compile(
    r"사업\s*(?:비|예산)|총\s*(?:사업비|예산|지원액|지원금|액)|합계|"
    r"소득|재산|연봉|매출|보증금|급여|임금|시급|일급|월급|세전|인건비|"
    r"기업|사업주|고용주|대출|융자|적금|저축|통장|상품권|포인트|바우처|쿠폰|"
    r"이용권|수강권|무료|교육|상담|서비스|프로그램|운영비|홍보비|참가비|이용료|사용료|입장료"
)
_NON_PERSONAL_REASONS = {
    "employer_support_not_cash_grant",
    "loan_or_financing_not_grant",
    "noncash_support_not_cash_grant",
    "savings_or_contribution_not_grant",
    "user_cost_not_grant",
    "wage_not_cash_grant",
}


@dataclass(frozen=True)
class _Component:
    start: int
    end: int
    amount_won: int
    amount_span: tuple[int, int]
    monthly: bool
    evidence_spans: tuple[tuple[int, int], ...]


def _segments(text: str) -> tuple[tuple[int, int], ...]:
    bounds = []
    cursor = 0
    for boundary in _BOUNDARY.finditer(text):
        left, right = cursor, boundary.start()
        while left < right and text[left].isspace():
            left += 1
        while right > left and text[right - 1].isspace():
            right -= 1
        if left < right:
            bounds.append((left, right))
        cursor = boundary.end()
    left, right = cursor, len(text)
    while left < right and text[left].isspace():
        left += 1
    while right > left and text[right - 1].isspace():
        right -= 1
    if left < right:
        bounds.append((left, right))
    return tuple(bounds)


def _literal_amount(text: str) -> tuple[int, tuple[int, int]] | None:
    candidates: dict[int, tuple[int, int]] = {}
    for match in _MONEY_RE.finditer(text):
        amount = _parse_money(match.group("money"))
        if amount is None:
            continue
        money = _Money(
            amount,
            match.start("money"),
            match.end("money"),
            match.group("money"),
        )
        if (
            not _has_support_anchor(text, money)
            or _amount_rejection(text, money)
            or _monthly_marker(text, money)
            or _annual_marker(text, money)
        ):
            continue
        candidates.setdefault(amount, (money.start, money.end))
    if len(candidates) != 1:
        return None
    amount, span = next(iter(candidates.items()))
    return amount, span


def _parse_component(text: str, start: int, end: int) -> _Component | None:
    segment = text[start:end]
    parsed = parse_source_payment(segment)
    facts = parsed.amountfact
    if facts is not None:
        if facts.pay_timing == "yearly":
            return None
        amount_span = next(
            (
                match.span("money")
                for match in _MONEY_RE.finditer(segment)
                if match.span("money") in facts.evidence_spans
            ),
            None,
        )
        if amount_span is None:
            return None
        return _Component(
            start,
            end,
            facts.amount_won,
            (start + amount_span[0], start + amount_span[1]),
            facts.amount_type == "monthly",
            tuple((start + left, start + right) for left, right in facts.evidence_spans),
        )
    if parsed.reasons != ("one_time_payment_timing_unknown",):
        return None
    literal = _literal_amount(segment)
    if literal is None:
        return None
    amount, (left, right) = literal
    return _Component(
        start,
        end,
        amount,
        (start + left, start + right),
        False,
        ((start + left, start + right),),
    )


def _other_money_spans(text: str, selected: tuple[int, int]) -> tuple[tuple[int, int], ...]:
    return tuple(
        match.span("money") for match in _MONEY_RE.finditer(text) if match.span("money") != selected
    )


def _has_unresolved_other_component(text: str, selected: tuple[int, int]) -> bool:
    segments = _segments(text)
    for left, right in _other_money_spans(text, selected):
        segment = next(
            ((start, end) for start, end in segments if start <= left < end),
            (0, len(text)),
        )
        context = text[segment[0] : segment[1]]
        if not _IGNORABLE_OTHER_MONEY.search(context):
            return True
    return False


def _select_component(text: str) -> tuple[_Component | None, tuple[str, ...], bool]:
    whole = _parse_component(text, 0, len(text))
    if whole is not None:
        if _has_unresolved_other_component(text, whole.amount_span):
            return None, ("decider_fixed_multiple_components",), False
        return whole, (), bool(_other_money_spans(text, whole.amount_span))

    found = [
        component
        for start, end in _segments(text)
        if (component := _parse_component(text, start, end)) is not None
    ]
    if len(found) > 1:
        return None, ("decider_fixed_multiple_components",), False
    if len(found) == 1:
        component = found[0]
        if _has_unresolved_other_component(text, component.amount_span):
            return None, ("decider_fixed_multiple_components",), False
        return component, (), True

    parsed = parse_source_payment(text)
    reasons = set(parsed.reasons)
    if _NON_GRANT.search(text) or re.search(r"(?:기업|사업주|고용주)(?:에게|에|당)", text):
        return None, ("decider_fixed_not_personal_cash",), False
    if "cash_amount_ambiguous" in reasons or "multiple_fixed_payment_amounts" in reasons:
        return None, ("decider_fixed_multiple_components",), False
    if reasons & _NON_PERSONAL_REASONS:
        return None, ("decider_fixed_not_personal_cash",), False
    if "cash_amount_not_fixed" in reasons or _CAP.search(text):
        return None, ("decider_fixed_cap_not_supported",), False
    return None, ("decider_fixed_single_source_component_required",), False


def compile_decider_fixed_cash(
    value: PolicyAnalysisInput,
    summary: dict,
    *,
    as_of: date,
    regions: Sequence[RegionOption] = (),
) -> Compilation:
    """Compile one literal fixed component; applicant facts resolve its month."""
    base = compile_api_decider_update(value, summary, as_of=as_of)
    normalized = dict(
        base.normalized,
        mode="decider-fixed-v1",
        calculation_ready=False,
        ai_review_calls=0,
        model_calls=0,
        requires_applicant_cash_runtime=True,
    )

    def hold(reason: str) -> Compilation:
        normalized["status"] = "held"
        return Compilation(None, (reason,), normalized)

    if base.payload is None:
        normalized["status"] = "held"
        return Compilation(None, base.reasons, normalized)

    document, binding = value.documents[0], value.collection_sources[0]
    if overlap_spans(document.text):
        return hold("decider_fixed_overlap_requires_mapping")

    fields = _fields(document.text)
    raw = {key: field.text.strip() for key, field in fields.items()}
    support_field = fields.get("plcySprtCn")
    if support_field is None or not support_field.text.strip():
        return hold("decider_fixed_support_required")
    target = next(policy for policy in value.policies if policy.id == value.policy_id)
    if raw.get("plcyPvsnMthdCd") in {"0042003", "0042007", "0042008", "0042009"}:
        return hold("decider_fixed_loan_tax_or_insurance")
    if _NON_GRANT.search(target.name):
        return hold("decider_fixed_not_personal_cash")
    if _ACTUAL_COST.search(target.name) and "정액" not in support_field.text:
        return hold("decider_fixed_actual_cost_not_fixed_grant")

    component, reasons, partial = _select_component(support_field.text)
    if component is None:
        return hold(reasons[0])
    selected_text = support_field.text[component.start : component.end]
    if (
        _NON_GRANT.search(selected_text)
        or re.search(r"(?:기업|사업주|고용주)(?:에게|에|당)", selected_text)
        or (
            re.search(r"기업|사업주|고용주", selected_text)
            and not _PERSONAL_RECIPIENT.search(selected_text)
        )
    ):
        return hold("decider_fixed_not_personal_cash")
    version = "sha256:" + binding.material_sha256 + DOCUMENT_VERSION_SUFFIX
    changed = replace(value, documents=(replace(document, document_version=version),))
    try:
        source_conditions, source_rules = compile_source_eligibility(changed, regions=regions)
    except (TypeError, ValueError):
        return hold("decider_fixed_eligibility_compilation_failed")
    if raw.get("zipCd") and not any(
        rule.get("field") in {"region_code", "domain_data.region.sido_code"}
        for rule in source_rules
    ):
        try:
            nationwide = decode_zip_codes(raw["zipCd"], regions).nationwide
        except (YouthcenterCodeError, TypeError, ValueError):
            nationwide = False
        if not nationwide:
            return hold("decider_fixed_residence_not_executable")

    payload = base.payload

    def versions(item):
        if isinstance(item, dict):
            return {
                key: (version if key == "document_version" else versions(child))
                for key, child in item.items()
            }
        if isinstance(item, list):
            return [versions(child) for child in item]
        return item

    payload = versions(payload)
    source = payload["benefit"]["source"]
    application, application_issues = source_application(raw, as_of=as_of)
    payload["benefit"]["application"] = application
    append_source_conditions(payload, source_conditions, source_rules)

    def evidence(left: int, right: int) -> dict:
        return {
            "quote": document.text[left:right],
            "location": f"chars:{left}-{right}",
            "source": source.copy(),
        }

    payment_evidence = []
    seen_locations = set()
    spans = [component.amount_span, *component.evidence_spans]
    for left, right in spans:
        item = evidence(left, right)
        if item["location"] not in seen_locations and item["quote"]:
            payment_evidence.append(item)
            seen_locations.add(item["location"])
    for offset in range(component.start, component.end, _MAX_EVIDENCE):
        left = support_field.start + offset
        right = support_field.start + min(offset + _MAX_EVIDENCE, component.end)
        item = evidence(left, right)
        if item["location"] not in seen_locations and item["quote"]:
            payment_evidence.append(item)
            seen_locations.add(item["location"])

    kind = "월 고정금액 1개월분" if component.monthly else "1회 고정지원금"
    label = f"공고상 {kind} {component.amount_won:,}원"
    if partial:
        label = "부분 계산(선택한 독립 지원 항목): " + label
    label += (
        "; 최대 기간·횟수·금액을 합산하지 않음; 실제 지급예정월·기수혜·"
        "중복 보전·추가 제외 조건은 신청자 확인 필요"
    )
    terms = [item for item in payment_evidence if len(item["quote"]) > 24]
    if not terms:
        terms = payment_evidence
    claim_id = "decider_fixed_claim"
    payload["benefit"]["eligibility_conditions"].append(
        {"id": claim_id, "description": label, "evidence": terms}
    )
    payload["conditions"].append(
        {
            "condition_id": claim_id,
            "kind": "source_cash",
            "source_cash": {
                "policy_id": value.policy_id,
                "mode": "fixed",
                "amount_won": component.amount_won,
                "reimbursement_bps": 10000,
            },
        }
    )
    lifetime = bool(_LIFETIME.search(selected_text))
    if lifetime:
        payload["benefit"]["eligibility_conditions"].append(
            {
                "id": "decider_fixed_no_previous",
                "description": "공고상 생애 1회: 이 정책의 기존 수혜 여부 확인",
                "evidence": terms,
            }
        )
        payload["conditions"].append(
            {
                "condition_id": "decider_fixed_no_previous",
                "kind": "none_of",
                "field": "domain_data.received",
                "values": [value.policy_id],
            }
        )

    payload["benefit"].update(
        summary=label,
        reviewed=True,
        lifetime_once=True if lifetime else None,
        benefit_amount={
            "type": "lump_sum",
            "amount_won": component.amount_won,
            "period_months": 1,
            "max_total_won": component.amount_won,
            "pay_timing": "once",
            "amount_text": label,
            "amount_status": "verified",
        },
    )
    payload.update(
        payments=None,
        payment_evidence=payment_evidence,
        unresolved_combination_rules=False,
    )
    normalized.update(
        status="verified",
        calculation_ready=True,
        amount_won=component.amount_won,
        claim_count=1,
        claim_mode="fixed",
        partial_component=partial,
        applicant_payment_month_required=True,
        stored_payment_schedule=None,
        application_issues=list(application_issues),
        source_version=version,
    )
    return Compilation(payload, (), normalized)


__all__ = ["compile_decider_fixed_cash"]
