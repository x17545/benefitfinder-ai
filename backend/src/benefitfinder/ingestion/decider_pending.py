"""Pure preparation of cash notices for the existing unreviewed catalogue path.

Successful preparation is ``pending_prepared``, never calculation or publication
approval. The writer must still verify the exact saved youthcenter projection.
Application status is evaluated at the input's checked_at, without reading a clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta, timezone
import math
import re
from urllib.parse import urlsplit

from benefitfinder.application.decider import Compilation, DocumentExtraction, KINDS, LocatedSpan
from benefitfinder.application.policy_analysis import PolicyAnalysisInput
from benefitfinder.ingestion.youthcenter_codes import CODE_GROUPS, decode_code_list


_DETAIL_URL = "https://www.youthcenter.go.kr/youthPolicy/ythPlcyTotalSearch/ythPlcyDetail/"
_HEADER = re.compile(r"(?m)^\[([A-Za-z][A-Za-z0-9]*)\]\r?\n")
_MONEY = re.compile(r"[0-9][0-9,.]*(?:\s*[억만천백십]\s*[0-9,.]*)*\s*원")
_DATE = r"(?:20[0-9]{6}|20[0-9]{2}[.-][0-9]{1,2}[.-][0-9]{1,2})"
_RANGE = re.compile(rf"\s*({_DATE})\s*[~∼～–—-]\s*({_DATE})\s*")
_EARLY_CLOSE = re.compile(
    r"별도\s*(?:공고|모집|신청)|예산\s*소진|조기\s*마감|"
    r"(?:기관별|시설별|차수별|회차별)\s*(?:모집|공고)"
)
_EMPLOYER = re.compile(
    r"(?:사업주|고용주|기업체|사업장|기업)(?:를|을)?\s*(?:대상|에게|에\s*(?:지원|지급))|"
    r"(?:사업주|고용주)\s*(?:지원|장려)"
)
_SERVICE = re.compile(
    r"무료(?:로)?\s*(?:제공|지원|이용)|"
    r"(?:교육|강좌|상담|컨설팅|프로그램|서비스)(?:만|을|를)?\s*(?:무료(?:로)?\s*)?제공|"
    r"원\s*상당.{0,60}(?:교육|강좌|상담|컨설팅|프로그램|서비스)"
)
_METADATA = frozenset({
    "plcyNo", "plcyNm", "lclsfNm", "mclsfNm", "rgtrInstCdNm", "sprvsnInstCdNm",
    "aplyUrlAddr", "refUrlAddr1", "refUrlAddr2",
})
_FIELD_LABELS = {
    "sprtTrgtAgeLmtYn": "연령 제한 여부", "sprtTrgtMinAge": "최소 연령",
    "sprtTrgtMaxAge": "최대 연령", "earnMinAmt": "소득 하한 원문",
    "earnMaxAmt": "소득 상한 원문", "earnEtcCn": "기타 소득 조건",
    "addAplyQlfcCndCn": "추가 신청 자격", "ptcpPrpTrgtCn": "참여 제한 대상",
    "etcMttrCn": "기타 사항", "zipCd": "지역 코드", "schoolCd": "학력 코드",
    "jobCd": "취업 상태 코드", "earnCndSeCd": "소득 조건 코드",
    "mrgSttsCd": "혼인 상태 코드", "sbizCd": "특화 조건 코드",
}


class _Hold(ValueError):
    pass


@dataclass(frozen=True)
class _Field:
    text: str
    start: int


def _fields(text: str) -> dict[str, _Field]:
    headers = list(_HEADER.finditer(text))
    if not headers or text[:headers[0].start()].strip():
        raise _Hold("pending_source_fields_invalid")
    fields = {}
    for index, header in enumerate(headers):
        key = header.group(1)
        if key in fields or len(key) > 100:
            raise _Hold("pending_source_field_duplicated_or_invalid")
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        # Remove only the projection's field separator, retaining the field value.
        if index + 1 < len(headers):
            if text[header.end():end].endswith("\r\n\r\n"):
                end -= 4
            elif text[header.end():end].endswith("\n\n"):
                end -= 2
        fields[key] = _Field(text[header.end():end], header.end())
    return fields


def _description(name: str, field: _Field) -> str:
    label = _FIELD_LABELS.get(name, name)
    if name in CODE_GROUPS:
        try:
            decoded = decode_code_list(name, field.text)
            labels = [item.label for item in decoded.codes if item.label is not None]
            if labels:
                label += " (" + ", ".join(labels) + ")"
        except (TypeError, ValueError):
            label += " (코드 의미 확인 필요)"
    return label + ": " + field.text


def _manual_conditions(fields: dict[str, _Field], source: dict) -> tuple[list[dict], list[dict]]:
    conditions = []
    rules = []
    for name, field in fields.items():
        if name in _METADATA or not field.text.strip():
            continue
        identity = "pending:" + name
        evidence = []
        for offset in range(0, len(field.text), 2500):
            quote = field.text[offset:offset + 2500]
            start = field.start + offset
            evidence.append({
                "quote": quote, "location": f"chars:{start}-{start + len(quote)}",
                "source": source.copy(),
            })
        conditions.append({
            "id": identity, "description": _description(name, field), "evidence": evidence,
        })
        rules.append({"condition_id": identity, "kind": "manual"})
    if len(conditions) > 100:
        raise _Hold("pending_manual_condition_limit_exceeded")
    return conditions, rules


def _date(text: str) -> date:
    parts = re.findall(r"[0-9]+", text)
    if len(parts) == 1 and len(parts[0]) == 8:
        raw = parts[0]
        return date(int(raw[:4]), int(raw[4:6]), int(raw[6:]))
    return date(*(int(part) for part in parts))


def _url(text: str) -> str | None:
    try:
        value = urlsplit(text)
        if (value.scheme in {"http", "https"} and value.hostname
                and value.username is None and value.password is None
                and not any(char.isspace() or ord(char) < 32 for char in text)):
            return text
    except ValueError:
        pass
    return None


def _application(fields: dict[str, _Field], checked_on: date) -> tuple[dict, str | None]:
    def get(name: str) -> str:
        return fields[name].text.strip() if name in fields else ""

    application = {
        "status": "unknown", "starts_on": None, "ends_on": None,
        "url": _url(get("aplyUrlAddr")),
    }
    match = _RANGE.fullmatch(get("aplyYmd"))
    if match is None:
        return application, "application_dates_unknown"
    try:
        start, end = (_date(token) for token in match.groups())
    except (TypeError, ValueError):
        return application, "application_dates_invalid"
    if start > end:
        return application, "application_dates_reversed"
    status = "upcoming" if checked_on < start else "closed" if end < checked_on else "open"
    if status == "open":
        text = " ".join(get(name) for name in (
            "plcySprtCn", "plcyAplyMthdCn", "etcMttrCn", "srngMthdCn",
        ))
        # Match the writer's existing open-state guards. In particular, business
        # dates can restrict opening but never become application endpoints.
        if (get("aplyPrdSeCd") != "0057001" or not get("plcyAplyMthdCn")
                or _EARLY_CLOSE.search(text)
                or re.findall(r"20[0-9]{6}", get("aplyYmd"))
                != [start.strftime("%Y%m%d"), end.strftime("%Y%m%d")]):
            return application, "application_open_unverified"
        if get("bizPrdEndYmd"):
            try:
                business_end = _date(get("bizPrdEndYmd"))
            except (TypeError, ValueError):
                return application, "application_business_end_invalid"
            if end > business_end:
                return application, "application_exceeds_business_end"
    application.update(status=status, starts_on=start.isoformat(), ends_on=end.isoformat())
    return application, None


def _compile(
    value: PolicyAnalysisInput, extracted: tuple[DocumentExtraction, ...], normalized: dict,
    as_of: date | None,
    *, source_only: bool = False,
) -> Compilation:
    if len(value.documents) != 1 or len(value.collection_sources) != 1:
        raise _Hold("pending_single_api_source_required")
    if not source_only and (len(extracted) != 1 or not isinstance(extracted[0], DocumentExtraction)):
        raise _Hold("pending_extraction_missing_or_invalid")
    document, binding = value.documents[0], value.collection_sources[0]
    extraction = None if source_only else extracted[0]
    if (not document.official or document.policy_id != value.policy_id
            or (extraction is not None and extraction.document != document)):
        raise _Hold("pending_extraction_source_mismatch")
    match = re.fullmatch(r"youth:(?:youthcenter:)?([0-9]+)", value.policy_id)
    if (match is None or binding.source != "youthcenter"
            or match.group(1) != binding.external_id
            or re.fullmatch(r"[0-9a-f]{64}", binding.material_sha256) is None):
        raise _Hold("pending_source_binding_mismatch")
    for span in (() if extraction is None else (*extraction.spans, *(extraction.raw_spans or ()))):
        if (not isinstance(span, LocatedSpan) or span.kind not in KINDS
                or type(span.start) is not int or type(span.end) is not int
                or not 0 <= span.start < span.end <= len(document.text)
                or document.text[span.start:span.end] != span.text
                or type(span.score) not in (int, float) or not math.isfinite(span.score)
                or not 0 <= span.score <= 1):
            raise _Hold("pending_extraction_span_invalid")
    decisions = {} if extraction is None else extraction.decisions
    if not isinstance(decisions, dict):
        raise _Hold("pending_extraction_missing_or_invalid")
    normalized.update(scope=decisions.get("scope"), amount_kind=decisions.get("amount_kind"))
    if not source_only and decisions.get("scope") != "youth_employment_or_housing":
        raise _Hold("pending_scope_not_youth")
    if not source_only and decisions.get("amount_kind") not in ("fixed", "cap", "variable"):
        raise _Hold("pending_cash_kind_unverified")

    fields = _fields(document.text)

    def get(name: str) -> str:
        return fields[name].text.strip() if name in fields else ""

    if get("plcyNo") != binding.external_id:
        raise _Hold("pending_source_policy_mismatch")
    target = next(policy for policy in value.policies if policy.id == value.policy_id)
    if not get("plcyNm") or get("plcyNm") != target.name:
        raise _Hold("pending_title_source_mismatch")
    labels = get("lclsfNm").split(",")
    mapping = {"일자리": "employment", "주거": "housing"}
    if any(label not in mapping for label in labels):
        raise _Hold("pending_source_category_out_of_scope")
    categories = list(dict.fromkeys(mapping[label] for label in labels))
    if set(categories) != set(target.categories):
        raise _Hold("pending_category_source_mismatch")
    provider = get("rgtrInstCdNm") or get("sprvsnInstCdNm")
    if not provider:
        raise _Hold("pending_provider_source_missing")
    support = fields.get("plcySprtCn")
    if support is None or _MONEY.search(support.text) is None:
        raise _Hold("pending_source_cash_amount_missing")
    audience = " ".join(get(name) for name in (
        "plcyNm", "plcyExplnCn", "plcySprtCn", "addAplyQlfcCndCn", "ptcpPrpTrgtCn",
    ))
    if _EMPLOYER.search(audience) or re.search(r"창업|사업운영|경영", get("mclsfNm")):
        raise _Hold("pending_employer_or_business_notice")
    if (_SERVICE.search(support.text)
            or get("plcyPvsnMthdCd") in {"0042001", "0042002", "0042011"}):
        raise _Hold("pending_service_notice")

    source = {
        "url": _DETAIL_URL + binding.external_id,
        "document_version": "sha256:" + binding.material_sha256,
        "checked_at": document.collected_at.isoformat(),
    }
    conditions, rules = _manual_conditions(fields, source)
    application, application_reason = _application(
        fields, as_of or document.collected_at.astimezone(timezone(timedelta(hours=9))).date(),
    )
    payload = {
        "benefit": {
            "id": value.policy_id, "domain": "youth", "title": get("plcyNm"),
            "summary": support.text, "provider": provider, "categories": categories,
            "application": application, "eligibility_conditions": conditions,
            "source": source, "domain_data": {},
            "benefit_amount": {
                "type": None, "amount_won": None, "period_months": None,
                "max_total_won": None, "pay_timing": None,
                "amount_text": support.text, "amount_status": "unknown",
            },
            "reviewed": False, "conflicts": [], "lifetime_once": None, "virtual_data": False,
        },
        "conditions": rules, "payments": None, "payment_evidence": [],
        "unresolved_combination_rules": True, "source_sha256": document.content_sha256,
        "collection_sources": [{
            "source": binding.source, "external_id": binding.external_id,
            "material_sha256": binding.material_sha256,
        }],
    }
    normalized.update(
        status="pending_prepared", pending_ready=True, application=application.copy(),
        application_reason=application_reason, source_sha256=document.content_sha256,
    )
    return Compilation(payload, (), normalized)


def compile_pending_policy(
    value: PolicyAnalysisInput, extracted: tuple[DocumentExtraction, ...],
    *, as_of: date | None = None,
) -> Compilation:
    """Prepare an unreviewed cash record; preserve unknown facts and manual conditions.

    No I/O, model calls, current clock, executable eligibility or payment schedule
    is used. The returned ``normalized.status`` distinguishes pending preparation
    from a verified compilation; the caller must use the unreviewed writer.
    """
    normalized = {"status": "held", "pending_ready": False, "calculation_ready": False}
    try:
        return _compile(value, extracted, normalized, as_of)
    except _Hold as error:
        return Compilation(None, (str(error),), normalized)
    except (AttributeError, ArithmeticError, KeyError, StopIteration, TypeError, ValueError):
        return Compilation(None, ("pending_compilation_failed",), normalized)


def compile_pending_source(value: PolicyAnalysisInput, *, as_of: date | None = None) -> Compilation:
    """MVP import of source facts only; no model invocation or fabricated decision.

    Calculation stays disabled until amount and payment facts are established.
    """
    normalized = {'status': 'held', 'pending_ready': False, 'calculation_ready': False,
                  'mode': 'source_only', 'model_analysis': 'not_run'}
    try:
        return _compile(value, (), normalized, as_of, source_only=True)
    except _Hold as error:
        return Compilation(None, (str(error),), normalized)
    except (AttributeError, ArithmeticError, KeyError, StopIteration, TypeError, ValueError):
        return Compilation(None, ('pending_compilation_failed',), normalized)
