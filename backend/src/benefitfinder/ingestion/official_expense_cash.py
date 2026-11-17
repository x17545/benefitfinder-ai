"""Compile a single explicit reimbursement from its official notice and API identity.

The primary notice supplies every published quotation. The API supplies a checked
policy identity, recruitment period and region code; neither source is silently
used to replace a contradictory fact in the other. No model or I/O is used.
"""

from collections.abc import Sequence
from datetime import date
import re

from benefitfinder.application.decider import Compilation
from benefitfinder.application.policy_analysis import PolicyAnalysisInput
from benefitfinder.application.region_options import RegionOption
from benefitfinder.ingestion.decider_pending import _fields
from benefitfinder.ingestion.source_application import source_application
from benefitfinder.ingestion.source_cash_dynamic import _claim
from benefitfinder.ingestion.source_overlap import overlap_spans
from benefitfinder.ingestion.source_payment_facts import _MONEY_RE, _parse_money
from benefitfinder.ingestion.youthcenter_codes import YouthcenterCodeError, decode_zip_codes


_SUPPORT = re.compile(r"(?m)^.*지원\s*(?:내용|금액|범위)[^\n]*(?:\n[^\n]+){0,5}")
_DATE = re.compile(r"(20\d{2})\s*[.년/-]\s*(\d{1,2})\s*[.월/-]\s*(\d{1,2})\s*[.일]?")
_OTHER_PROGRAM = re.compile(
    r"타\s*(?:기관|지자체|사업)|유사\s*사업|"
    r"참여\s*중인?\s*자[^\n]{0,30}(?:제외|불가)|"
    r"(?:수당|제도|사업)[^\n]{0,30}참여[^\n]{0,30}(?:제외|불가)|"
    r"제외\s*대상[^\n]{0,100}(?:수당|제도)"
)


def _normalized(text: str) -> str:
    return re.sub(r"[^가-힣A-Za-z0-9]", "", text).lower()


def _recruitment_agrees(text: str, application: dict) -> bool:
    """Compare explicitly labelled notice dates; keep budget exhaustion conditional."""
    matched = False
    for label in re.finditer(r"(?:신청|접수|모집)\s*(?:기간|기한)", text):
        block = text[label.end():label.end() + 240]
        next_heading = re.search(r"\n\s*[□○*]\s*(?:지원|신청|접수|제출|자격)", block)
        if next_heading:
            block = block[:next_heading.start()]
        raw = list(_DATE.finditer(block))
        if not raw:
            continue
        try:
            dates = [date(*(int(part) for part in match.groups())).isoformat() for match in raw]
        except ValueError:
            return False
        if len(dates) > 2 or dates[0] != application["starts_on"]:
            return False
        if len(dates) == 2 and dates[1] != application["ends_on"]:
            return False
        if len(dates) == 1 and "예산" not in block:
            return False
        matched = True
    return matched


def _residence(raw: str, text: str, regions: Sequence[RegionOption]):
    coverage = decode_zip_codes(raw, regions)
    # HOLD means a known, restricted coverage that needs an executable rule;
    # it does not mean that its codes are unknown or invalid.
    if coverage.nationwide or not coverage.groups:
        return None
    known = {region.sido_code: region.sido_name for region in regions}
    known.update({district.code: district.name
                  for region in regions for district in region.sigungu})
    ambiguous = {name for name in known.values() if list(known.values()).count(name) > 1}
    provinces = {district.code: region.sido_name
                 for region in regions for district in region.sigungu}
    codes, spans = [], []
    for group in coverage.groups:
        name = known.get(group.code)
        if not name:
            return None
        matches = [match for match in re.finditer(r"[^\n]+", text)
                   if name in match.group() and re.search(r"주민등록|거주", match.group())
                   and (name not in ambiguous or provinces.get(group.code, "") in match.group())]
        if not matches:
            return None
        codes.append(group.code)
        spans.append(matches[0].span())
    if len({len(code) for code in codes}) != 1:
        return None
    field = "region_code" if len(codes[0]) == 5 else "domain_data.region.sido_code"
    return field, codes, spans


def compile_official_expense_cash(
    value: PolicyAnalysisInput, *, as_of: date, regions: Sequence[RegionOption] = (),
) -> Compilation:
    """Prepare one capped expense claim; missing expense and payment month stay unknown."""
    normalized = {"mode": "official-expense-source-v1", "calculation_ready": False,
                  "model_calls": 0, "ai_review_calls": 0}

    def hold(reason: str) -> Compilation:
        return Compilation(None, ("official_expense_" + reason,), normalized)

    bindings = {source.source: source for source in value.collection_sources}
    if (len(value.documents) != 2 or len(value.collection_sources) != 2
            or set(bindings) != {"official_documents", "youthcenter"}):
        return hold("primary_and_api_required")
    primary = next((doc for doc in value.documents if doc.document_version ==
                    "official-v1:" + bindings["official_documents"].material_sha256), None)
    api = next((doc for doc in value.documents if doc.document_version ==
                "sha256:" + bindings["youthcenter"].material_sha256), None)
    if primary is None or api is None or primary is api:
        return hold("source_version_mismatch")
    if value.document_sources:
        linked = {item.document_id: (item.source, item.external_id) for item in value.document_sources}
        if (linked.get(primary.id) != ("official_documents", bindings["official_documents"].external_id)
                or linked.get(api.id) != ("youthcenter", bindings["youthcenter"].external_id)):
            return hold("document_binding_mismatch")
    raw = {key: field.text.strip() for key, field in _fields(api.text).items()}
    target = next(policy for policy in value.policies if policy.id == value.policy_id)
    labels = set(raw.get("lclsfNm", "").split(","))
    external_id = bindings["youthcenter"].external_id
    if (not external_id.isascii() or not external_id.isdigit()
            or value.policy_id not in {"youth:" + external_id, "youth:youthcenter:" + external_id}
            or raw.get("plcyNo") != external_id
            or raw.get("plcyNm") != target.name
            or len(_normalized(target.name)) < 6
            or _normalized(target.name) not in _normalized(primary.text)):
        return hold("identity_mismatch")
    categories = {key for name, key in (("일자리", "employment"), ("주거", "housing")) if name in labels}
    if (not labels <= {"일자리", "주거"} or not labels or categories != set(target.categories)
            or ("일자리" in labels and not set(raw.get("mclsfNm", "").split(",")) <= {
                "취업", "재직자", "권익보호",
            })):
        return hold("outside_mvp")
    text = primary.text
    if (not re.search(r"지원\s*(?:대상|자격)|신청\s*(?:자격|대상)", text)
            or not re.search(r"(?:신청|접수)\s*(?:방법|절차|제출|안내)|신청서", text)):
        return hold("complete_terms_required")
    if any(overlap_spans(doc.text) or _OTHER_PROGRAM.search(doc.text) for doc in value.documents):
        return hold("overlap_requires_mapping")
    application, issues = source_application(raw, as_of=as_of)
    if (issues or not application["starts_on"] or not application["ends_on"]
            or not _recruitment_agrees(text, application)):
        return hold("recruitment_conflict_or_missing")
    try:
        residence = _residence(raw.get("zipCd", ""), text, regions)
    except (YouthcenterCodeError, TypeError, ValueError):
        residence = None
    if residence is None:
        return hold("residence_not_executable")
    claims = []
    for block in _SUPPORT.finditer(text):
        if not _MONEY_RE.search(block.group()):
            continue
        if re.search(r"각각|교통비|차등|가족\s*수|종류별", block.group()):
            return hold("multiple_or_variable_expenses")
        claim = _claim(block.group(), target.name)
        if claim is None or claim[0] != "expense":
            return hold("explicit_reimbursement_required")
        claims.append((claim[1], [(block.start() + lo, block.start() + hi) for lo, hi in claim[2]]))
    if not claims or len({amount for amount, _ in claims}) != 1:
        return hold("single_cap_required")
    amount = claims[0][0]
    for line in re.finditer(r"[^\n]+", text):
        if re.search(r"지급액|지원액|지원금액|지원한도|최대|한도|상한", line.group()):
            if re.search(r"소득|재산|자산|보증금|사업비|예산", line.group()):
                continue
            if any(_parse_money(money.group()) != amount for money in _MONEY_RE.finditer(line.group())):
                return hold("contradictory_cap")
    source = {"url": primary.source_url, "document_version": primary.document_version + ":cash-rules-v2",
              "checked_at": primary.collected_at.isoformat()}

    def evidence(start: int, end: int) -> dict:
        return {"quote": text[start:end], "location": f"chars:{start}-{end}", "source": source.copy()}

    conditions, rules = [], []
    for start in range(0, len(text), 2400):
        identity = "official_terms_" + str(start // 2400)
        conditions.append({"id": identity, "description": "공식 공고의 자격·예외·지원 범위 확인",
                           "evidence": [evidence(start, min(start + 2400, len(text)))]})
        rules.append({"condition_id": identity, "kind": "manual"})
    field, codes, spans = residence
    conditions.append({"id": "residence", "description": "공고의 주민등록·거주 지역 확인",
                       "evidence": [evidence(lo, hi) for lo, hi in spans]})
    rules.append({"condition_id": "residence", "kind": "selection", "field": field, "values": codes})
    label = f"1건 실비 지원: 실제 적격 지출액과 {amount:,}원 상한 중 작은 금액"
    money_evidence = [evidence(lo, hi) for _, locations in claims for lo, hi in locations]
    conditions.append({"id": "source_cash_claim", "description": label + "; 지급월·기수혜·중복 보전·제외 조건 확인 필요",
                       "evidence": money_evidence})
    rules.append({"condition_id": "source_cash_claim", "kind": "source_cash", "source_cash": {
        "policy_id": value.policy_id, "mode": "expense", "amount_won": amount, "reimbursement_bps": 10000}})
    lifetime = re.search(r"생애\s*(?:1\s*회|한\s*번)|평생\s*1\s*회", text)
    if lifetime:
        conditions.append({"id": "no_previous_grant", "description": "공고상 생애 1회: 이 정책의 과거 수혜 이력 확인",
                           "evidence": [evidence(*lifetime.span())]})
        rules.append({"condition_id": "no_previous_grant", "kind": "none_of",
                      "field": "domain_data.received", "values": [value.policy_id]})
    payload = {"benefit": {"id": value.policy_id, "domain": "youth", "title": target.name,
        "summary": label, "provider": raw.get("sprvsnInstCdNm") or raw.get("rgtrInstCdNm") or "공식 공고 기관",
        "categories": list(target.categories), "application": application, "source": source,
        "domain_data": {}, "eligibility_conditions": conditions, "benefit_amount": {
            "type": "lump_sum", "amount_won": amount, "period_months": 1, "max_total_won": amount,
            "pay_timing": "once", "amount_text": label + "; 실제 지급예정월 입력 시 계산", "amount_status": "verified"},
        "reviewed": True, "conflicts": [], "lifetime_once": True if lifetime else None, "virtual_data": False},
        "conditions": rules, "payments": None, "payment_evidence": money_evidence,
        "source_sha256": primary.content_sha256, "unresolved_combination_rules": False,
        "collection_sources": [{"source": item.source, "external_id": item.external_id,
                                "material_sha256": item.material_sha256} for item in value.collection_sources]}
    normalized.update(calculation_ready=True, claim_mode="expense", amount_won=amount,
                      stored_payment_schedule=None, claim_count=1)
    return Compilation(payload, (), normalized)
