"""Compile one source-backed claim without guessing its cost or payment month.

This optional path needs the applicant-facts calculator. It never emits a fixed
payment schedule. The stored amount is a cap for expenses, and only one claim
is counted even if the source permits several claims over a year.
"""

from collections.abc import Sequence
from dataclasses import replace
from datetime import date
import re

from benefitfinder.application.decider import Compilation
from benefitfinder.application.policy_analysis import PolicyAnalysisInput
from benefitfinder.application.region_options import RegionOption
from benefitfinder.ingestion.decider_pending import _fields
from benefitfinder.ingestion.source_application import source_application
from benefitfinder.ingestion.source_eligibility import compile_source_eligibility
from benefitfinder.ingestion.source_overlap import overlap_spans
from benefitfinder.ingestion.source_payment_facts import (
    _Money, _MONEY_RE, _amount_rejection, _has_support_anchor, _monthly_marker,
    _parse_money,
)
from benefitfinder.ingestion.youthcenter_codes import YouthcenterCodeError, decode_zip_codes


_EXPENSE = re.compile(r"응시료|응시\s*비용|이사비|중개\s*(?:보수|수수료)|보증료")
_REIMBURSEMENT = re.compile(r"실비|실제\s*(?:부담|납부|지출|소요)|전액|100\s*%")
_PERIODIC = re.compile(r"매월|매달|월별|월마다|분기|반기|학기|매년|연간|(?<![가-힣])연\s*\d|/\s*월")
_NON_GRANT = re.compile(
    r"대출|융자|보증금\s*지원|임대주택|기숙사|통장|적금|저축|대여|인건비|임금|시급|세전|"
    r"개\s*(?:소|사)\s*당|사업주|인턴|고용장려금|수강권|이용권|수강료|기업\s*(?:지원|인증|선정)|우수기업"
)
_MONEY_CAP = re.compile(r"최대|한도|상한|이내|이하|범위|\d\s*만\s*원\s*내")
_MAX_CHUNK = 2400


def _claim(text: str, title: str) -> tuple[str, int, tuple[tuple[int, int], ...]] | None:
    """Recognize a narrow reimbursement or one unambiguous personal grant."""
    if _NON_GRANT.search(title) or _NON_GRANT.search(text):
        return None
    percentages = re.findall(r"(\d+(?:\.\d+)?)\s*%", text)
    if any(value != "100" for value in percentages):
        return None
    if re.search(r"차등|구간별|선정.{0,20}조정|\d\s*[~∼～]\s*\d", text):
        return None

    expense = bool(_EXPENSE.search(title) and _EXPENSE.search(text))
    if expense and "정액" in text:
        expense = False
    if expense:
        if not _REIMBURSEMENT.search(text):
            return None
        candidates: dict[int, list[tuple[int, int]]] = {}
        for match in _MONEY_RE.finditer(text):
            amount = _parse_money(match.group())
            if amount is None:
                return None
            # An income/deposit threshold or project budget is never a claim cap.
            line_start = text.rfind("\n", 0, match.start()) + 1
            line_end = text.find("\n", match.end())
            line = text[line_start:line_end if line_end >= 0 else len(text)]
            if re.search(r"소득|재산|보증금|사업비|예산|세대당|기혼|미혼", line):
                return None
            if not (_EXPENSE.search(line) or _MONEY_CAP.search(line)):
                return None
            candidates.setdefault(amount, []).append(match.span())
        if len(candidates) != 1:
            return None
        amount, spans = next(iter(candidates.items()))
        return "expense", amount, tuple(spans)

    if _PERIODIC.search(text) or re.search(
        r"(?:\d+|두|세)\s*(?:개월|년|회)\s*(?:간|분할)|분할|[*×xX]\s*\d+\s*(?:개월|년|회)", text
    ):
        return None
    candidates = {}
    for match in _MONEY_RE.finditer(text):
        amount = _parse_money(match.group())
        if amount is None:
            return None
        money = _Money(amount, match.start(), match.end(), match.group())
        if not _has_support_anchor(text, money) or _amount_rejection(text, money):
            return None
        if _monthly_marker(text, money):
            return None
        candidates.setdefault(amount, []).append(match.span())
    if len(candidates) != 1:
        return None
    amount, spans = next(iter(candidates.items()))
    return "fixed", amount, tuple(spans)


def compile_source_cash_dynamic(
    value: PolicyAnalysisInput, *, as_of: date, regions: Sequence[RegionOption] = (),
) -> Compilation:
    """Prepare a single claim for explicit applicant cost and calendar facts."""
    normalized = {"mode": "source-applicant-cash-v1", "model_calls": 0,
                  "ai_review_calls": 0, "calculation_ready": False,
                  "requires_applicant_cash_runtime": True}

    def hold(reason: str) -> Compilation:
        return Compilation(None, (reason,), normalized)

    if (len(value.documents) != 1 or len(value.collection_sources) != 1
            or value.collection_sources[0].source != "youthcenter"):
        return hold("source_dynamic_single_api_source_required")
    doc, binding = value.documents[0], value.collection_sources[0]
    target = next(policy for policy in value.policies if policy.id == value.policy_id)
    fields = _fields(doc.text)
    raw = {key: field.text.strip() for key, field in fields.items()}
    if (not doc.official or doc.policy_id != value.policy_id
            or raw.get("plcyNo") != binding.external_id
            or fields.get("plcyNm") is None or fields["plcyNm"].text != target.name):
        return hold("source_dynamic_identity_mismatch")
    labels = set(raw.get("lclsfNm", "").split(","))
    categories = [key for label, key in (("일자리", "employment"), ("주거", "housing"))
                  if label in labels]
    if (not labels or not labels <= {"일자리", "주거"}
            or ("일자리" in labels and not set(raw.get("mclsfNm", "").split(",")) <= {
                "취업", "재직자", "권익보호",
            })):
        return hold("source_dynamic_outside_mvp")
    if raw.get("plcyPvsnMthdCd") in {"0042003", "0042007", "0042008", "0042009"}:
        return hold("source_dynamic_loan_tax_or_insurance")
    if overlap_spans(doc.text):
        return hold("source_dynamic_overlap_requires_mapping")
    support = fields.get("plcySprtCn")
    if support is None:
        return hold("source_dynamic_support_required")
    claim = _claim(support.text, target.name)
    if claim is None:
        return hold("source_dynamic_single_claim_not_supported")
    mode, amount, spans = claim
    doc = replace(doc, document_version=f"sha256:{binding.material_sha256}:source-facts-v1")
    value = replace(value, documents=(doc,))
    source = {"url": doc.source_url, "document_version": doc.document_version,
              "checked_at": doc.collected_at.isoformat()}

    def evidence(start: int, end: int) -> dict:
        return {"quote": doc.text[start:end], "location": f"chars:{start}-{end}",
                "source": source.copy()}

    conditions, rules = compile_source_eligibility(value, regions=regions)
    if raw.get("zipCd") and not any(rule.get("field") in {
        "region_code", "domain_data.region.sido_code",
    } for rule in rules):
        try:
            nationwide = decode_zip_codes(raw["zipCd"], regions).nationwide
        except (YouthcenterCodeError, TypeError, ValueError):
            nationwide = False
        if not nationwide:
            return hold("source_dynamic_residence_not_executable")
    payment_evidence = [evidence(support.start + lo, support.start + hi) for lo, hi in spans]
    # Include the complete support clause as well as the exact amount spans.
    terms = [evidence(support.start + start,
                      support.start + min(start + _MAX_CHUNK, len(support.text)))
             for start in range(0, len(support.text), _MAX_CHUNK)]
    label = (f"1건 실비 지원: 실제 적격 지출액과 {amount:,}원 상한 중 작은 금액" if mode == "expense"
             else f"원문상 1건 지원금 {amount:,}원")
    description = label + "; 해당 정책의 실제 지급예정월·기수혜·중복 보전·추가 제외 조건 확인 필요"
    conditions.append({"id": "source_cash_claim", "description": description, "evidence": terms})
    rules.append({"condition_id": "source_cash_claim", "kind": "source_cash",
                  "source_cash": {"policy_id": value.policy_id, "mode": mode,
                                  "amount_won": amount, "reimbursement_bps": 10000}})
    lifetime_once = bool(re.search(r"생애\s*(?:1\s*회|한\s*번)|평생\s*1\s*회", doc.text))
    if lifetime_once:
        conditions.append({"id": "source_cash_no_previous", "description": "공고상 생애 1회: 이 정책의 기존 수혜 여부 확인",
                           "evidence": terms})
        rules.append({"condition_id": "source_cash_no_previous", "kind": "none_of",
                      "field": "domain_data.received", "values": [value.policy_id]})
    application, issues = source_application(raw, as_of=as_of)
    if issues:
        normalized["application_issues"] = list(issues)
    amount_text = label + "; 연간 최대 횟수 미합산, 실제 지급예정월 입력 시 계산"
    payload = {
        "benefit": {"id": value.policy_id, "domain": "youth", "title": target.name,
                    "summary": amount_text, "provider": raw.get("sprvsnInstCdNm") or raw.get("rgtrInstCdNm") or "공식 공고 기관",
                    "categories": categories, "application": application,
                    "eligibility_conditions": conditions, "source": source, "domain_data": {},
                    "benefit_amount": {"type": "lump_sum", "amount_won": amount,
                        "period_months": 1, "max_total_won": amount, "pay_timing": "once",
                        "amount_text": amount_text, "amount_status": "verified"},
                    "reviewed": True, "conflicts": [], "lifetime_once": True if lifetime_once else None,
                    "virtual_data": False},
        "conditions": rules, "payments": None, "payment_evidence": payment_evidence,
        "unresolved_combination_rules": False, "source_sha256": doc.content_sha256,
        "collection_sources": [{"source": binding.source, "external_id": binding.external_id,
                                "material_sha256": binding.material_sha256}],
    }
    normalized.update(calculation_ready=True, claim_mode=mode, amount_won=amount,
                      application_status=application["status"], stored_payment_schedule=None,
                      claim_count=1, actual_expense_required=mode == "expense")
    return Compilation(payload, (), normalized)
