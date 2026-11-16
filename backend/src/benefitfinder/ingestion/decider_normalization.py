"""Deterministically compile narrow Decider policy answers into catalogue input.

The Decider thresholds in this module are conservative release gates, not a
calibrated statement that a policy fact is true.  Automatic publication after
these checks was explicitly requested for this pipeline.  A successful result
still requires exact source offsets, an explicit overlap statement, complete
dates, and a payment schedule that can be represented by the existing
CatalogRecord shape without guesswork.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from benefitfinder.application.decider import (
    Compilation,
    DocumentExtraction,
    KINDS,
    LocatedSpan,
)
from benefitfinder.application.policy_analysis import PolicyAnalysisInput
from benefitfinder.application.policy_rules import SourceDocument
from benefitfinder.application.region_options import RegionOption
from benefitfinder.ingestion.decider_source_fields import prepare_source_fields
from benefitfinder.ingestion.rent_support import rent_support_candidate, rent_support_record
from benefitfinder.ingestion.youthcenter_codes import (
    CodeState,
    InvalidRegionCatalogError,
    YouthcenterCodeError,
    decode_code,
    decode_code_list,
    decode_zip_codes,
)

MAX_WON = 2**53 - 1
_MIN_CONSISTENCY = 0.95
_MIN_READINESS = 1.95
_DETAIL_URL = "https://www.youthcenter.go.kr/youthPolicy/ythPlcyTotalSearch/ythPlcyDetail/"
_FIELD_HEADER = re.compile(r"(?m)^\[([A-Za-z][A-Za-z0-9]*)\]\r?\n?")
_EXTERNAL_ID = re.compile(r"^[0-9]{1,200}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_BIG_UNITS = {
    "천만": Decimal(10_000_000),
    "백만": Decimal(1_000_000),
    "십만": Decimal(100_000),
    "만": Decimal(10_000),
    "천": Decimal(1_000),
}
_MONEY = re.compile(
    rf"(?<![\d,])"
    rf"(?:(?P<ocheon>{_NUMBER})\s*억"
    rf"(?:\s*(?P<sub>{_NUMBER})\s*(?P<subunit>천만|백만|십만|만|천))?\s*원"
    rf"|(?P<plain>{_NUMBER})\s*(?P<unit>천만|백만|십만|만|천)?\s*원)"
)
_MONEY_RESIDUE = re.compile(r"\d[\d,]*(?:\.\d+)?\s*(?:억|천만|백만|십만|만|천)?\s*원")
_DURATION = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(개월|년)")

_ALLOWED_COVERAGE = {"complete", "incomplete", "uncertain"}
_ALLOWED_AMOUNT = {"fixed", "cap", "variable", "none", "uncertain"}
_ALLOWED_ELIGIBILITY = {"simple", "complex", "uncertain"}
_ALLOWED_OVERLAP = {"none_explicit", "restricted", "uncertain"}
_ALLOWED_SCOPE = {"youth_employment_or_housing", "business", "other", "uncertain"}
_SPAN_METHODS = frozenset({"locate", "source_field", "source_context"})
_JOB_SELECTION_VALUES = frozenset(
    {
        "high_school",
        "student",
        "grad_student",
        "job_seeker",
        "employed",
        "short_term_worker",
        "self_employed",
        "freelancer",
        "founder",
        "farmer",
        "other",
    }
)
_SCHOOL_SELECTION_VALUES = frozenset(
    {
        "high_school_below",
        "high_school_enrolled",
        "high_school_graduate",
        "university_enrolled",
        "university_expected",
        "university_graduate",
        "masters_doctorate",
    }
)
_MARITAL_SELECTION_VALUES = frozenset({"single", "married", "marrying_soon"})
_SPECIAL_SELECTION_VALUES = frozenset(
    {
        "basic_livelihood",
        "near_poverty",
        "single_parent",
        "disabled",
        "sme_employee",
        "farmer_fisher",
        "soldier",
        "none",
    }
)


@dataclass(frozen=True)
class _FieldValue:
    value: str
    start: int
    end: int


@dataclass(frozen=True)
class _Money:
    value: int
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class _PaymentFacts:
    amount_won: int
    period_months: int
    max_total_won: int
    benefit_type: str
    pay_timing: str
    payments: tuple[dict[str, int], ...]
    amount_text: str
    evidence_spans: tuple[LocatedSpan, ...]


def _issue(issues: list[str], code: str) -> None:
    if code not in issues:
        issues.append(code)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _number(value: object) -> float | None:
    if type(value) not in (int, float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _external_id(policy_id: object) -> str | None:
    if not isinstance(policy_id, str):
        return None
    if policy_id.startswith("youth:youthcenter:"):
        external_id = policy_id.removeprefix("youth:youthcenter:")
    elif policy_id.startswith("youth:"):
        external_id = policy_id.removeprefix("youth:")
    else:
        return None
    return external_id if _EXTERNAL_ID.fullmatch(external_id) else None


def _field_values(text: str, issues: list[str]) -> dict[str, _FieldValue]:
    headers = list(_FIELD_HEADER.finditer(text))
    values: dict[str, _FieldValue] = {}
    for index, header in enumerate(headers):
        key = header.group(1)
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        value_start = header.end()
        raw = text[value_start:end]
        leading = len(raw) - len(raw.lstrip())
        trailing = len(raw) - len(raw.rstrip())
        start = value_start + leading
        value_end = end - trailing
        value = text[start:value_end]
        if key in values:
            _issue(issues, "source_field_duplicated")
            continue
        values[key] = _FieldValue(value, start, value_end)
    return values


def _span_body(span: LocatedSpan) -> str:
    body = span.text
    marker = re.match(r"^\[[A-Za-z][A-Za-z0-9]*\]\r?\n", body)
    return body[marker.end() :].strip() if marker is not None else body.strip()


def _location(span: LocatedSpan) -> str:
    return f"chars:{span.start}-{span.end}"


def _evidence(span: LocatedSpan, source: dict[str, str]) -> dict[str, object]:
    return {
        "quote": span.text,
        "location": _location(span),
        "source": source.copy(),
    }


def _evidence_record(span: LocatedSpan, document: SourceDocument) -> dict[str, object]:
    return {
        "kind": span.kind,
        "document_id": document.id,
        "start": span.start,
        "end": span.end,
        "text": span.text,
        "sha256": _sha256(span.text),
        "score": span.score,
        "method": span.method,
    }


def _valid_spans(extraction: DocumentExtraction, issues: list[str]) -> tuple[LocatedSpan, ...]:
    text = extraction.document.text
    valid: list[LocatedSpan] = []
    seen: set[tuple[str, int, int, str]] = set()
    for span in extraction.spans:
        method = getattr(span, "method", "locate")
        key = (
            getattr(span, "kind", ""),
            getattr(span, "start", -1),
            getattr(span, "end", -1),
            method if isinstance(method, str) else "",
        )
        if (
            not isinstance(span, LocatedSpan)
            or span.kind not in KINDS
            or span.method not in _SPAN_METHODS
        ):
            _issue(issues, "evidence_span_invalid")
            continue
        if (
            type(span.start) is not int
            or type(span.end) is not int
            or not 0 <= span.start < span.end <= len(text)
            or text[span.start : span.end] != span.text
            or not isinstance(span.text, str)
            or not span.text.strip()
            or _number(span.score) is None
            or not 0 <= span.score <= 1
        ):
            _issue(issues, "evidence_span_invalid")
            continue
        if len(span.text) > 2500:
            _issue(issues, "evidence_quote_too_long")
            continue
        if key not in seen:
            valid.append(span)
            seen.add(key)
    return tuple(sorted(valid, key=lambda item: (item.start, item.end, item.kind)))


def _spans(spans: tuple[LocatedSpan, ...], kind: str) -> tuple[LocatedSpan, ...]:
    return tuple(span for span in spans if span.kind == kind)


def _parse_money(text: str) -> tuple[_Money, ...]:
    mentions: list[_Money] = []
    occupied: list[tuple[int, int]] = []
    for match in _MONEY.finditer(text):
        try:
            if match.group("ocheon") is not None:
                total = Decimal(match.group("ocheon").replace(",", "")) * Decimal(100_000_000)
                if match.group("sub") is not None:
                    subunit = _BIG_UNITS.get(match.group("subunit") or "")
                    if subunit is None:
                        raise ValueError
                    total += Decimal(match.group("sub").replace(",", "")) * subunit
            else:
                unit = match.group("unit")
                multiplier = Decimal(1) if unit is None else _BIG_UNITS[unit]
                total = Decimal(match.group("plain").replace(",", "")) * multiplier
        except (InvalidOperation, KeyError, TypeError, ValueError):
            raise ValueError("invalid_money") from None
        if total != total.to_integral_value() or not 0 <= total <= MAX_WON:
            raise ValueError("invalid_money")
        mentions.append(_Money(int(total), match.start(), match.end(), match.group(0)))
        occupied.append((match.start(), match.end()))
    residue = list(text)
    for start, end in occupied:
        residue[start:end] = " " * (end - start)
    if _MONEY_RESIDUE.search("".join(residue)):
        raise ValueError("invalid_money")
    return tuple(mentions)


def _duration_months(text: str) -> tuple[int, ...]:
    values: set[int] = set()
    for match in _DURATION.finditer(text):
        amount = Decimal(match.group(1))
        months = amount * (12 if match.group(2) == "년" else 1)
        if months != months.to_integral_value():
            raise ValueError("invalid_duration")
        value = int(months)
        if not 1 <= value <= 1200:
            raise ValueError("invalid_duration")
        values.add(value)
    return tuple(sorted(values))


def _amount_is_ambiguous(text: str, mentions: tuple[_Money, ...]) -> bool:
    if len(mentions) != 1:
        return True
    mention = mentions[0]
    before = text[max(0, mention.start - 24) : mention.start]
    after = text[mention.end : mention.end + 12]
    if re.search(r"(?:최대|최고|상한|한도)\s*(?:금액[은는]?)?\s*$", before):
        return True
    if re.match(r"\s*(?:이내|한도|상한|부터|이상|초과)", after):
        return True
    return (
        re.search(
            r"단계별|단계|구간별|차등|수요자별|실비|차감|공제|감액|"
            r"(?:대상\s*)?제외|예외|단\s*,|내외|정도|수준|분할|회차|"
            r"\d+\s*회|(?<!계)약",
            text,
        )
        is not None
    )


def _monthly_amount(text: str, mention: _Money) -> bool:
    before = text[max(0, mention.start - 16) : mention.start]
    return re.search(r"(?:매월|매달|월별|월)\s*$", before) is not None


def _explicit_lump_month(text: str) -> int | None:
    months: set[int] = set()
    for match in re.finditer(r"(\d+)\s*개월\s*(?:뒤|후|차|째)", text):
        months.add(int(match.group(1)))
    for match in re.finditer(r"(\d+)\s*년\s*(?:뒤|후|차|째)", text):
        months.add(int(match.group(1)) * 12)
    if re.search(r"(?:선정|선발|합격)(?:자)?\s*(?:후\s*)?(?:즉시|바로)", text):
        months.add(0)
    if re.search(r"지원\s*(?:개시|시작)\s*(?:월|시점|일)?\s*(?:부터|즉시|에)", text):
        months.add(1)
    candidate_months = [month for month in months if 0 <= month <= 1200]
    return candidate_months[0] if len(candidate_months) == 1 else None


def _payment_facts(
    amount_spans: tuple[LocatedSpan, ...],
    duration_spans: tuple[LocatedSpan, ...],
    timing_spans: tuple[LocatedSpan, ...],
    issues: list[str],
    normalized: dict,
) -> _PaymentFacts | None:
    if not amount_spans:
        _issue(issues, "amount_evidence_missing")
        return None
    if len(amount_spans) != 1:
        _issue(issues, "amount_evidence_multiple")
        return None
    amount_span = amount_spans[0]
    amount_text = _span_body(amount_span)
    try:
        mentions = _parse_money(amount_text)
    except ValueError:
        _issue(issues, "amount_unparseable")
        return None
    if mentions:
        normalized["amount_won"] = mentions[0].value
    if len(mentions) == 1:
        normalized["payment_month"] = None
        mention = mentions[0]
        before = amount_text[max(0, mention.start - 24) : mention.start]
        after = amount_text[mention.end : mention.end + 12]
        if re.search(r"(?:최대|최고|상한|한도)\s*(?:금액[은는]?)?\s*$", before):
            _issue(issues, "amount_cap_not_fixed")
            return None
        if re.match(r"\s*(?:이내|한도|상한)", after):
            _issue(issues, "amount_cap_not_fixed")
            return None
    if _amount_is_ambiguous(amount_text, mentions):
        _issue(issues, "amount_not_single_fixed")
        return None
    mention = mentions[0]
    timing_text = " ".join(
        (_span_body(span) for span in duration_spans + timing_spans if span != amount_span)
    )
    combined = f"{amount_text} {timing_text}"
    monthly = (
        _monthly_amount(amount_text, mention)
        or re.search(r"매월|매달|월별", timing_text) is not None
    )
    if re.search(r"\d+\s*(?:개월|월)\s*(?:차|째)?\s*부터", combined):
        _issue(issues, "payment_start_offset_unsupported")
        return None

    evidence_spans = [amount_span]
    if monthly:
        if (re.search(r"지원\s*시작월부터|지원\s*첫\s*달부터|첫\s*지원월부터", combined) is None
            or re.search(r"(?:매월|매달).{0,100}지급", combined) is None):
            _issue(issues, "monthly_payment_anchor_missing")
            return None
        duration_text = amount_text
        if duration_spans:
            if len(duration_spans) != 1:
                _issue(issues, "duration_evidence_multiple")
                return None
            duration_text = _span_body(duration_spans[0])
            evidence_spans.append(duration_spans[0])
        if timing_spans:
            if len(timing_spans) != 1:
                _issue(issues, "payment_timing_ambiguous")
                return None
            evidence_spans.append(timing_spans[0])
        if re.search(r"최대|최장|이내|미만|초과|이상|약|정도|내외", duration_text):
            _issue(issues, "duration_not_fixed")
            return None
        try:
            months = _duration_months(duration_text)
        except ValueError:
            _issue(issues, "duration_unparseable")
            return None
        if len(months) != 1:
            _issue(issues, "duration_missing" if not months else "duration_ambiguous")
            return None
        period = months[0]
        total = mention.value * period
        if total > MAX_WON:
            _issue(issues, "amount_total_exceeds_limit")
            return None
        facts = _PaymentFacts(
            amount_won=mention.value,
            period_months=period,
            max_total_won=total,
            benefit_type="monthly",
            pay_timing="monthly_from_start",
            payments=tuple(
                {"month": month, "amount_won": mention.value} for month in range(1, period + 1)
            ),
            amount_text=amount_text,
            evidence_spans=tuple(evidence_spans),
        )
        normalized.update(
            {
                "period_months": period,
                "max_total_won": total,
                "pay_timing": facts.pay_timing,
                "payments": list(facts.payments),
            }
        )
        return facts

    if len(timing_spans) > 1:
        _issue(issues, "payment_timing_ambiguous")
        return None
    timing_span = timing_spans[0] if timing_spans else None
    anchor_text = amount_text
    if timing_span is not None:
        anchor_text += " " + _span_body(timing_span)
    month = _explicit_lump_month(anchor_text)
    if month is None:
        _issue(
            issues,
            "payment_timing_unknown"
            if timing_span is not None
            else "payment_timing_evidence_missing",
        )
        return None
    if month > 1200:
        _issue(issues, "payment_month_exceeds_limit")
        return None
    if timing_span is not None:
        evidence_spans.append(timing_span)
    facts = _PaymentFacts(
        amount_won=mention.value,
        period_months=max(1, month),
        max_total_won=mention.value,
        benefit_type="lump_sum",
        pay_timing="once",
        payments=({"month": month, "amount_won": mention.value},),
        amount_text=amount_text,
        evidence_spans=tuple(evidence_spans),
    )
    normalized.update(
        {
            "payment_month": month,
            "period_months": max(1, month),
            "max_total_won": mention.value,
            "pay_timing": facts.pay_timing,
            "payments": list(facts.payments),
        }
    )
    return facts


_AGE_RANGE = (
    re.compile(r"만\s*(\d{1,2})\s*세\s*이상\s*(\d{1,2})\s*세\s*이하"),
    re.compile(
        r"만?\s*(\d{1,2})\s*세\s*이상\s*[,，]?\s*(?:이며\s*)?"
        r"(?:만\s*)?(\d{1,2})\s*세\s*이하"
    ),
    re.compile(r"만\s*(\d{1,2})\s*(?:세)?\s*[~∼\-]\s*(\d{1,2})\s*세"),
    re.compile(r"(?<![\d.])(\d{1,2})\s*세\s*이상\s*(\d{1,2})\s*세\s*이하"),
)
_SELECTIONS = (
    (
        re.compile(r"취업\s*준비생|구직자|구직\s*중|미취업(?:자|청년|상태)?"),
        "employment_status",
        "job_seeker",
    ),
    (re.compile(r"재직자|재직\s*중"), "employment_status", "employed"),
    (re.compile(r"대학원생"), "domain_data.school", "masters_doctorate"),
    (re.compile(r"대학생|대학교\s*재학생"), "domain_data.school", "university_enrolled"),
    (re.compile(r"고등학생"), "domain_data.school", "high_school_enrolled"),
    (re.compile(r"미혼"), "domain_data.marital", "single"),
    (re.compile(r"기혼"), "domain_data.marital", "married"),
    (re.compile(r"결혼\s*예정"), "domain_data.marital", "marrying_soon"),
    (re.compile(r"무주택"), "domain_data.housing.home_ownership", "homeless"),
    (re.compile(r"주택\s*소유|자가"), "domain_data.housing.home_ownership", "owned"),
    (re.compile(r"세대주"), "domain_data.housing.household_head", "head"),
    (re.compile(r"세대원"), "domain_data.housing.household_head", "member"),
    (re.compile(r"월세"), "domain_data.contract.type", "monthly_rent"),
    (re.compile(r"전세"), "domain_data.contract.type", "jeonse"),
    (re.compile(r"기숙사"), "domain_data.housing.living", "dorm"),
    (re.compile(r"기초생활(?:보장)?\s*수급자"), "domain_data.special", "basic_livelihood"),
    (re.compile(r"차상위(?:\s*계층)?"), "domain_data.special", "near_poverty"),
    (re.compile(r"한부모"), "domain_data.special", "single_parent"),
    (re.compile(r"장애인"), "domain_data.special", "disabled"),
    (re.compile(r"농어업인|농업인|어업인"), "domain_data.special", "farmer_fisher"),
)
_UNSUPPORTED_ELIGIBILITY = re.compile(
    r"거주|주민등록|전입|지역\s*(?:요건|제한|대상)|자산|가구|부양|성적|학점|어학|"
    r"병역|군\s*복무|사업자|업종|매출|고용보험|건강보험|납부|모집인원|선착순|"
    r"여성|남성|자녀|양육|휴학|신청일\s*기준|중위소득|기준\s*중위소득|"
    r"졸업|재학|수료|학위|전공|국적|외국인|재산|부모|소상공인|창업|"
    r"기업\s*(?:규모|재직|소속)|근로자|가점|우선\s*선정|예비\s*창업|"
    r"대상\s*제외|선정\s*제외|지원\s*제외"
)


def _unsupported_eligibility(text: str) -> bool:
    residual = list(text)
    for pattern in (*_AGE_RANGE, *(item[0] for item in _SELECTIONS)):
        for match in pattern.finditer(text):
            residual[match.start() : match.end()] = " " * (match.end() - match.start())
    return _UNSUPPORTED_ELIGIBILITY.search("".join(residual)) is not None


def _age_rules(text: str) -> tuple[tuple[int, int, str], ...]:
    rules: dict[tuple[int, int], str] = {}
    for pattern in _AGE_RANGE:
        for match in pattern.finditer(text):
            minimum, maximum = int(match.group(1)), int(match.group(2))
            if minimum > maximum or not 0 <= minimum <= maximum <= 120:
                raise ValueError("invalid_age")
            rules[(minimum, maximum)] = match.group(0)
    return tuple((minimum, maximum, text) for minimum, maximum in rules)


def _income_max(text: str) -> tuple[int, bool, str] | None:
    if "소득" not in text:
        return None
    if re.search(r"월\s*소득|월소득", text):
        raise ValueError("invalid_income")
    mentions = _parse_money(text)
    if len(mentions) != 1:
        if mentions:
            raise ValueError("invalid_income")
        return None
    mention = mentions[0]
    suffix = text[mention.end : mention.end + 24]
    prefix = text[max(0, mention.start - 24) : mention.start]
    if re.search(r"이상|초과", suffix) or "중위소득" in text:
        raise ValueError("invalid_income")
    if re.search(r"이하", suffix):
        return mention.value, True, text
    if re.search(r"미만", suffix):
        return mention.value, False, text
    if re.search(r"(?:최대|한도)\s*$", prefix):
        return mention.value, True, text
    raise ValueError("invalid_income")


def _selection_rules(text: str) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (field, value, match.group(0))
        for pattern, field, value in _SELECTIONS
        if (match := pattern.search(text)) is not None
    )


def _parse_eligibility(
    spans: tuple[LocatedSpan, ...],
    fields: dict[str, _FieldValue],
    source: dict[str, str],
    issues: list[str],
    normalized: dict,
    text: str,
    regions: Sequence[RegionOption],
) -> tuple[list[dict], list[dict]]:
    if not spans:
        _issue(issues, "eligibility_evidence_missing")
        return [], []
    age: dict[tuple[int, int], tuple[str, set[tuple[int, int]]]] = {}
    income: dict[tuple[int, bool], tuple[str, set[tuple[int, int]]]] = {}
    selected: dict[str, dict[str, object]] = {}
    for span in spans:
        if span.method == "source_field":
            continue
        body = _span_body(span)
        if not body:
            _issue(issues, "eligibility_evidence_empty")
            continue
        matched = False
        try:
            ages = _age_rules(body)
            for minimum, maximum, fragment in ages:
                matched = True
                description, ranges = age.setdefault((minimum, maximum), (fragment, set()))
                ranges.add((span.start, span.end))
                age[(minimum, maximum)] = (description, ranges)
        except ValueError:
            _issue(issues, "eligibility_age_invalid")
            continue
        try:
            income_rule = _income_max(body)
            if income_rule is not None:
                matched = True
                maximum, inclusive, fragment = income_rule
                description, ranges = income.setdefault((maximum, inclusive), (fragment, set()))
                ranges.add((span.start, span.end))
                income[(maximum, inclusive)] = (description, ranges)
        except ValueError:
            _issue(issues, "eligibility_income_invalid")
        selections = _selection_rules(body)
        if selections:
            matched = True
        for field, value, fragment in selections:
            item = selected.setdefault(
                field,
                {"values": [], "fragments": [], "ranges": set()},
            )
            if value not in item["values"]:
                item["values"].append(value)
            if fragment not in item["fragments"]:
                item["fragments"].append(fragment)
            item["ranges"].add((span.start, span.end))
        if not matched:
            _issue(issues, "eligibility_rule_unsupported")
        if _unsupported_eligibility(body):
            _issue(issues, "eligibility_rule_unsupported")

    if len(age) > 1:
        _issue(issues, "eligibility_age_contradictory")
    if len(income) > 1:
        _issue(issues, "eligibility_income_contradictory")

    span_by_range = {(span.start, span.end): span for span in spans}
    benefit_conditions: list[dict] = []
    executable: list[dict] = []

    def add_condition(
        condition_id: str,
        description: str,
        ranges: set[tuple[int, int]],
        rule: dict[str, object],
    ) -> None:
        evidence = [
            _evidence(span_by_range[location], source)
            for location in sorted(ranges)
            if location in span_by_range
        ]
        if not evidence:
            _issue(issues, "eligibility_evidence_missing")
            return
        benefit_conditions.append(
            {"id": condition_id, "description": description.strip(), "evidence": evidence}
        )
        executable.append({"condition_id": condition_id, **rule})

    def add_field_range(*names: str) -> tuple[int, int] | None:
        present = [
            fields[name]
            for name in names
            if name in fields and fields[name].value.strip()
        ]
        if not present:
            return None
        start = min(field.start for field in present)
        end = max(field.end for field in present)
        location = (start, end)
        span_by_range[location] = LocatedSpan(
            kind="eligibility",
            start=start,
            end=end,
            text=text[start:end],
            score=0.0,
        )
        return location

    def merge_selection(
        field: str,
        values: Sequence[str],
        fragment: str,
        location: tuple[int, int],
    ) -> None:
        existing = selected.get(field)
        if existing is not None:
            if set(existing["values"]) != set(values):
                _issue(issues, "source_coded_eligibility_contradictory")
                return
            if fragment not in existing["fragments"]:
                existing["fragments"].append(fragment)
            existing["ranges"].add(location)
            return
        selected[field] = {
            "values": list(dict.fromkeys(values)),
            "fragments": [fragment],
            "ranges": {location},
        }

    # The official API flag is inverted from its field name: Y means no age
    # restriction; N means the min/max metadata describes an age restriction.
    age_flag = fields.get("sprtTrgtAgeLmtYn")
    if age_flag is not None and age_flag.value.strip():
        marker = age_flag.value.strip().upper()
        if marker not in {"Y", "N"}:
            _issue(issues, "source_age_flag_invalid")
        elif marker == "Y":
            if age:
                _issue(issues, "source_age_flag_contradictory")
        else:
            try:
                minimum = int(fields["sprtTrgtMinAge"].value.strip())
                maximum = int(fields["sprtTrgtMaxAge"].value.strip())
                if not 0 <= minimum <= maximum <= 120 or minimum == maximum == 0:
                    raise ValueError("invalid_age_range")
            except (KeyError, ValueError):
                _issue(issues, "eligibility_age_missing")
            else:
                expected = (minimum, maximum)
                if age and set(age) != {expected}:
                    _issue(issues, "eligibility_age_contradictory")
                elif not age:
                    location = add_field_range("sprtTrgtMinAge", "sprtTrgtMaxAge")
                    if location is None:
                        _issue(issues, "eligibility_age_missing")
                    else:
                        age[expected] = (
                            f"만 {minimum}세 이상 {maximum}세 이하",
                            {location},
                        )
    elif any(
        field is not None and field.value.strip() not in {"", "0"}
        for field in (
            fields.get("sprtTrgtMinAge"),
            fields.get("sprtTrgtMaxAge"),
        )
    ):
        _issue(issues, "source_age_flag_missing")

    def income_amount(name: str, *, default: int | None = None) -> int | None:
        field = fields.get(name)
        if field is None or not field.value.strip():
            return default
        if not re.fullmatch(r"[0-9]+", field.value.strip()):
            raise ValueError("invalid_income")
        return int(field.value.strip())

    earn_code = fields.get("earnCndSeCd")
    if earn_code is not None and earn_code.value.strip():
        try:
            decoded_income = decode_code("earnCndSeCd", earn_code.value)
        except YouthcenterCodeError:
            _issue(issues, "source_coded_eligibility_requires_mapping")
        else:
            try:
                minimum = income_amount("earnMinAmt", default=0)
                maximum = income_amount("earnMaxAmt", default=0)
            except ValueError:
                _issue(issues, "eligibility_income_invalid")
            else:
                if decoded_income.state is CodeState.UNRESTRICTED:
                    if income or minimum != 0 or maximum != 0:
                        _issue(issues, "source_coded_eligibility_contradictory")
                elif decoded_income.normalized == "annual_income":
                    if (
                        minimum is None
                        or maximum is None
                        or minimum != 0
                        or not 0 < maximum <= MAX_WON
                    ):
                        _issue(issues, "eligibility_income_invalid")
                    elif income and set(income) != {(maximum, True)}:
                        _issue(issues, "eligibility_income_contradictory")
                    elif not income:
                        location = add_field_range("earnMinAmt", "earnMaxAmt")
                        if location is None:
                            _issue(issues, "eligibility_income_missing")
                        else:
                            income[(maximum, True)] = (
                                f"연 소득 {maximum:,}원 이하",
                                {location},
                            )
                else:
                    _issue(issues, "source_coded_eligibility_requires_mapping")
    elif any(
        field is not None and field.value.strip() not in {"", "0"}
        for field in (fields.get("earnMinAmt"), fields.get("earnMaxAmt"))
    ):
        _issue(issues, "source_coded_eligibility_requires_mapping")

    major = fields.get("plcyMajorCd")
    if major is not None and major.value.strip():
        try:
            decoded_major = decode_code_list("plcyMajorCd", major.value)
        except YouthcenterCodeError:
            _issue(issues, "source_major_requirement_unknown")
        else:
            # The current profile has no verified major taxonomy. Preserve this
            # official restriction rather than treating omitted major rules as met.
            if decoded_major.state is not CodeState.UNRESTRICTED:
                _issue(issues, "source_major_requirement_requires_review")

    code_fields = (
        ("jobCd", "employment_status", _JOB_SELECTION_VALUES),
        ("schoolCd", "domain_data.school", _SCHOOL_SELECTION_VALUES),
        ("mrgSttsCd", "domain_data.marital", _MARITAL_SELECTION_VALUES),
        ("sbizCd", "domain_data.special", _SPECIAL_SELECTION_VALUES),
    )
    for source_field, target_field, allowed_values in code_fields:
        field = fields.get(source_field)
        if field is None or not field.value.strip():
            continue
        try:
            decoded = decode_code_list(source_field, field.value)
        except YouthcenterCodeError:
            _issue(issues, "source_coded_eligibility_requires_mapping")
            continue
        if decoded.contradictory:
            _issue(issues, "source_coded_eligibility_contradictory")
            continue
        if decoded.state is CodeState.UNRESTRICTED:
            if selected.get(target_field) is not None:
                _issue(issues, "source_coded_eligibility_contradictory")
            continue
        values = [code.normalized for code in decoded.codes]
        if any(value is None or value not in allowed_values for value in values):
            _issue(issues, "source_coded_eligibility_requires_mapping")
            continue
        location = add_field_range(source_field)
        if location is None:
            _issue(issues, "eligibility_evidence_missing")
            continue
        fragment = f"{source_field}: " + ", ".join(
            f"{code.code}({code.label})" for code in decoded.codes
        )
        merge_selection(
            target_field,
            [str(value) for value in values],
            fragment,
            location,
        )

    zip_field = fields.get("zipCd")
    if zip_field is not None and zip_field.value.strip():
        try:
            coverage = decode_zip_codes(zip_field.value, regions)
        except (InvalidRegionCatalogError, YouthcenterCodeError):
            _issue(issues, "source_coded_eligibility_requires_mapping")
        else:
            if not coverage.nationwide:
                markers = {
                    "36110" if region.sido_code == "36" else region.sido_code + "000": region
                    for region in regions
                }
                if not coverage.groups:
                    _issue(issues, "source_coded_eligibility_requires_mapping")
                elif all(group.code in markers for group in coverage.groups):
                    target = "domain_data.region.sido_code"
                    values = [markers[group.code].sido_code for group in coverage.groups]
                else:
                    target = "region_code"
                    values = []
                    for group in coverage.groups:
                        region = markers.get(group.code)
                        if region is None:
                            values.append(group.code)
                        else:
                            values.extend(district.code for district in region.sigungu)
                location = add_field_range("zipCd")
                if location is None:
                    _issue(issues, "eligibility_evidence_missing")
                else:
                    merge_selection(
                        target,
                        list(dict.fromkeys(values)),
                        "zipCd: " + ", ".join(group.name for group in coverage.groups),
                        location,
                    )

    for index, (minimum, maximum) in enumerate(sorted(age), start=1):
        description, ranges = age[(minimum, maximum)]
        add_condition(
            "age" if index == 1 else f"age_{index}",
            description,
            ranges,
            {
                "kind": "age",
                "minimum": minimum,
                "maximum": maximum,
                "military_extension_years": 0,
            },
        )
    for index, (maximum, inclusive) in enumerate(sorted(income), start=1):
        description, ranges = income[(maximum, inclusive)]
        add_condition(
            "income_max" if index == 1 else f"income_max_{index}",
            description,
            ranges,
            {"kind": "income_max", "maximum": maximum, "inclusive": inclusive},
        )
    for index, (field, item) in enumerate(sorted(selected.items()), start=1):
        condition_id = "selection_" + re.sub(r"[^a-z0-9]+", "_", field).strip("_")
        if index > 1 and any(c["id"] == condition_id for c in benefit_conditions):
            condition_id += f"_{index}"
        add_condition(
            condition_id,
            " / ".join(str(fragment) for fragment in item["fragments"]),
            set(item["ranges"]),
            {"kind": "selection", "field": field, "values": list(item["values"])},
        )

    normalized["conditions"] = executable
    return benefit_conditions, executable


_EXPLICIT_NO_OVERLAP = re.compile(
    r"(?:(?:다른|타|모든)\s*(?:청년\s*)?(?:정책|지원사업|지원금|급여)(?:과|와|의)?\s*)?"
    r"(?:중복\s*(?:수혜|수급|지원)\s*(?:제한\s*없음|가능|허용|할\s*수\s*있음)"
    r"|동시\s*수급\s*(?:가능|할\s*수\s*있음))[.。]?"
)
_OVERLAP_RESTRICTION = re.compile(
    r"(?:중복\s*(?:수혜|지원|참여|신청)\s*"
    r"(?:불가|금지|제한(?!\s*없음)|할\s*수\s*없음))|"
    r"(?:동시\s*(?:수급|지원)\s*(?:불가|금지))"
)


def _overlap(
    spans: tuple[LocatedSpan, ...],
    kind: str | None,
    issues: list[str],
    normalized: dict,
    source_text: str,
) -> None:
    if kind != "none_explicit":
        _issue(issues, f"overlap_{kind or 'missing'}")
        return
    if not spans:
        _issue(issues, "overlap_evidence_missing")
        return
    texts = tuple(_span_body(span) for span in spans)
    if any(_OVERLAP_RESTRICTION.search(text) for text in texts):
        _issue(issues, "overlap_restriction_present")
        return
    # Applying to two schemes is not permission to receive both. A named pair
    # allowing overlap is also not blanket permission with every other policy.
    # Check the complete source line even when Locate selected only a fragment.
    statements = []
    for span in spans:
        start = source_text.rfind('\n', 0, span.start) + 1
        end = source_text.find('\n', span.end)
        statements.append(source_text[start:len(source_text) if end < 0 else end].strip())
    if not all(_EXPLICIT_NO_OVERLAP.fullmatch(text) for text in statements):
        _issue(issues, "overlap_not_explicit")
    normalized["overlap"] = {"kind": kind, "statements": list(texts)}


def _exceptions(spans: tuple[LocatedSpan, ...], issues: list[str], normalized: dict) -> None:
    if not spans:
        return
    statements = [_span_body(span) for span in spans if _span_body(span)]
    normalized["exceptions"] = statements
    clear_absence = re.compile(r"(?:별도\s*)?예외(?:\s*사항)?(?:은|는|가|이)?\s*없(?:습니다|음)")
    substantive = [clear_absence.sub("", statement).strip(" .。") for statement in statements]
    if any(substantive) and any(
        re.search(r"예외|제외|단\s*,|차감|공제|감액", statement) for statement in statements
    ):
        _issue(issues, "exceptions_unsupported")


_DOTTED_DATE = re.compile(r"(?<!\d)(\d{4})\.(\d{2})\.(\d{2})(?!\d)")
_KOREAN_DATE = re.compile(r"(?<!\d)(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일")
_COMPACT_DATE = re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)")


def _dates(text: str) -> tuple[date, ...]:
    found: list[tuple[int, date]] = []
    matched_ranges: list[tuple[int, int]] = []
    for pattern in (_DOTTED_DATE, _KOREAN_DATE, _COMPACT_DATE):
        for match in pattern.finditer(text):
            try:
                parsed = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            except ValueError:
                raise ValueError("invalid_date") from None
            found.append((match.start(), parsed))
            matched_ranges.append((match.start(), match.end()))
    for start, end in matched_ranges:
        for other_start, other_end in matched_ranges:
            if (start, end) != (other_start, other_end) and start < other_end and other_start < end:
                raise ValueError("invalid_date")
    residue = list(text)
    for start, end in matched_ranges:
        residue[start:end] = " " * (end - start)
    if re.search(r"\d{4}\s*[.\-년]\s*\d{1,2}\s*[.\-월]\s*\d{1,2}", "".join(residue)):
        raise ValueError("invalid_date")
    return tuple(parsed for _, parsed in sorted(found))


def _today() -> date:
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _application(
    spans: tuple[LocatedSpan, ...],
    fields: dict[str, _FieldValue],
    issues: list[str],
    normalized: dict,
) -> dict[str, object] | None:
    aply_ymd = fields.get("aplyYmd")
    ranges: set[tuple[date, date]] = set()
    texts: list[str] = []
    if aply_ymd is not None and aply_ymd.value.strip():
        texts.append(aply_ymd.value)
    texts.extend(_span_body(span) for span in spans if _span_body(span))
    for text in texts:
        try:
            dates = _dates(text)
        except ValueError:
            _issue(issues, "application_dates_invalid")
            continue
        if len(dates) == 2:
            ranges.add((dates[0], dates[1]))
        elif len(dates) > 2:
            _issue(issues, "application_dates_contradictory")
    if not ranges:
        _issue(issues, "application_deadline_unknown")
        normalized["application"] = {
            "status": "unknown",
            "starts_on": None,
            "ends_on": None,
            "url": None,
        }
        return None
    if len(ranges) != 1:
        _issue(issues, "application_dates_contradictory")
        return None
    start, end = next(iter(ranges))
    if start > end:
        _issue(issues, "application_dates_reversed")
        return None
    today = _today()
    status = "upcoming" if start > today else "open" if end >= today else "closed"
    url = None
    application_url = fields.get("aplyUrlAddr")
    if application_url is not None and application_url.value.strip():
        parsed = urlsplit(application_url.value.strip())
        if (
            parsed.scheme in ("http", "https")
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
            and not any(char.isspace() for char in application_url.value)
        ):
            url = application_url.value.strip()
    application = {
        "status": status,
        "starts_on": start.isoformat(),
        "ends_on": end.isoformat(),
        "url": url,
    }
    normalized["application"] = application
    normalized["starts_on"] = start.isoformat()
    normalized["ends_on"] = end.isoformat()
    normalized["status"] = status
    return application


def _payload(
    value: PolicyAnalysisInput,
    title: str,
    summary: str,
    provider: str,
    categories: list[str],
    source: dict[str, str],
    application: dict[str, object],
    benefit_conditions: list[dict],
    executable: list[dict],
    payment: _PaymentFacts,
    document: SourceDocument,
    source_sha256: str,
    external_id: str,
    material_sha256: str,
    issues: list[str],
) -> dict | None:
    if len(title.strip()) > 120 or not title.isprintable():
        _issue(issues, "policy_title_invalid")
    if not 1 <= len(title) <= 120:
        _issue(issues, "policy_title_invalid")
    if not summary.strip() or not provider.strip():
        _issue(issues, "policy_summary_or_provider_missing")
    if not categories or len(categories) != len(set(categories)):
        _issue(issues, "policy_categories_invalid")
    if any(category not in {"employment", "housing"} for category in categories):
        _issue(issues, "policy_categories_invalid")
    if any(item.get("id") is None for item in benefit_conditions):
        _issue(issues, "eligibility_rule_unsupported")
    if {item["id"] for item in benefit_conditions} != {item["condition_id"] for item in executable}:
        _issue(issues, "eligibility_rule_unsupported")
    if issues:
        return None
    payment_evidence = [
        _evidence(span, source)
        for span in sorted(payment.evidence_spans, key=lambda item: (item.start, item.end))
    ]
    payload = {
        "benefit": {
            "id": value.policy_id,
            "domain": "youth",
            "title": title,
            "summary": summary,
            "provider": provider,
            "categories": categories,
            "application": application,
            "eligibility_conditions": benefit_conditions,
            "source": source.copy(),
            "domain_data": {},
            "benefit_amount": {
                "type": payment.benefit_type,
                "amount_won": payment.amount_won,
                "period_months": payment.period_months,
                "max_total_won": payment.max_total_won,
                "pay_timing": payment.pay_timing,
                "amount_text": payment.amount_text,
                "amount_status": "verified",
            },
            "reviewed": True,
            "conflicts": [],
            "lifetime_once": None,
            "virtual_data": False,
        },
        "conditions": executable,
        "payments": list(payment.payments),
        "payment_evidence": payment_evidence,
        "unresolved_combination_rules": False,
        "source_sha256": source_sha256,
        "collection_sources": [
            {
                "source": "youthcenter",
                "external_id": external_id,
                "material_sha256": material_sha256,
            }
        ],
    }
    try:
        json.dumps(payload, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, OverflowError):
        _issue(issues, "payload_not_serializable")
        return None
    if any(evidence["quote"] not in document.text for evidence in payment_evidence):
        _issue(issues, "evidence_quote_hash_mismatch")
        return None
    return payload


def _compile(
    value: PolicyAnalysisInput,
    extracted: tuple[DocumentExtraction, ...],
    *,
    regions: Sequence[RegionOption] = (),
) -> Compilation:
    issues: list[str] = []
    normalized: dict = {
        "policy_id": getattr(value, "policy_id", None),
        "coverage": None,
        "amount_kind": None,
        "eligibility_kind": None,
        "overlap_kind": None,
        "scope": None,
        "consistent": None,
        "readiness": None,
        "amount_won": None,
        "period_months": None,
        "max_total_won": None,
        "payment_month": None,
        "pay_timing": None,
        "payments": [],
        "application": None,
        "conditions": [],
        "evidence": [],
    }
    external_id = _external_id(getattr(value, "policy_id", None))
    if external_id is None:
        _issue(issues, "policy_id_invalid")

    policies = getattr(value, "policies", ())
    target = next(
        (policy for policy in policies if getattr(policy, "id", None) == value.policy_id),
        None,
    )
    if target is None:
        _issue(issues, "analysis_policy_missing")

    documents = getattr(value, "documents", ())
    bindings = getattr(value, "collection_sources", ())
    document_sources = getattr(value, "document_sources", ())
    if len(extracted) != 1:
        _issue(issues, "multiple_documents_unsupported")
    if len(documents) != 1:
        _issue(issues, "multiple_documents_unsupported")
    if len(bindings) != 1:
        _issue(issues, "multiple_sources_unsupported")
    if document_sources:
        _issue(issues, "document_sources_unsupported")
    extraction = (
        next(
            (item for item in extracted if item.document.policy_id == value.policy_id),
            extracted[0] if extracted else None,
        )
        if extracted
        else None
    )
    if extraction is None:
        _issue(issues, "document_missing")
        return Compilation(None, tuple(issues), normalized)
    document = extraction.document
    if not isinstance(document, SourceDocument):
        _issue(issues, "document_invalid")
        return Compilation(None, tuple(issues), normalized)
    if not document.official:
        _issue(issues, "document_not_official")
    if document.policy_id != value.policy_id:
        _issue(issues, "document_policy_mismatch")
    if document not in documents:
        _issue(issues, "document_not_in_input")

    binding = None
    if bindings:
        binding = next(
            (
                item
                for item in bindings
                if item.source == "youthcenter" and item.external_id == external_id
            ),
            None,
        )
        if binding is None:
            _issue(issues, "source_binding_mismatch")
        elif external_id is None or binding.external_id != external_id:
            _issue(issues, "source_binding_mismatch")
        if binding is not None and not _SHA256.fullmatch(binding.material_sha256):
            _issue(issues, "source_material_hash_invalid")
    else:
        _issue(issues, "source_binding_missing")

    source_sha256 = _sha256(document.text)
    normalized["source_sha256"] = source_sha256
    canonical_source: dict[str, str] | None = None
    if (
        binding is not None
        and external_id is not None
        and _SHA256.fullmatch(binding.material_sha256)
    ):
        canonical_source = {
            "url": _DETAIL_URL + external_id,
            "document_version": "sha256:" + binding.material_sha256,
            "checked_at": document.collected_at.isoformat(),
        }
        normalized["source"] = canonical_source.copy()

    decisions = extraction.decisions if isinstance(extraction.decisions, dict) else {}
    structured_first = decisions.get("analysis_mode") == "structured_first"
    if structured_first:
        # Routing completeness is not a model's confidence or semantic approval.
        # Recompute the plan from the immutable original, never from AI answers.
        prepared = prepare_source_fields(document.text)
        audit = extraction.preparation or {}
        expected_body = [asdict(field) for field in prepared.body]
        if (audit.get("structured") != prepared.structured
                or audit.get("body_fields") != expected_body):
            _issue(issues, "source_preparation_mismatch")
        for issue in prepared.issues:
            _issue(issues, issue)
        normalized.update(analysis_mode="structured_first", structured_fields=prepared.structured,
                          body_fields=expected_body, semantic_review_required=bool(prepared.body),
                          analysis_usage={key: audit.get(key) for key in (
                              "source_characters", "body_characters", "model_requests",
                              "question_count", "followup_requests")})
        # Retain the entire residual source even when locate misses a condition.
        # These candidates cannot be auto-published as verified executable rules.
        if prepared.body:
            _issue(issues, "body_rules_require_review")
        income = prepared.structured.get("earnCndSeCd", {})
        if "annual_income" in income.get("values", []):
            _issue(issues, "source_income_unit_unverified")
        fields_for_kind = _field_values(document.text, issues)
        labels = set(fields_for_kind.get("lclsfNm", _FieldValue("", 0, 0)).value.split(","))
        normalized["scope"] = ("youth_employment_or_housing"
                               if labels and labels <= {"일자리", "주거"} else "uncertain")
        if normalized["scope"] == "uncertain":
            _issue(issues, "scope_uncertain")
        support_body = fields_for_kind.get("plcySprtCn", _FieldValue("", 0, 0)).value
        try:
            money = _parse_money(support_body)
            normalized["amount_kind"] = ("fixed" if not _amount_is_ambiguous(support_body, money)
                                         else "variable" if money else "uncertain")
        except ValueError:
            normalized["amount_kind"] = "uncertain"
        # Existing full-source overlap validation still checks any apparent grant.
        overlap_spans = tuple(span for span in extraction.spans if span.kind == "overlap")
        statements = []
        for span in overlap_spans:
            left = document.text.rfind("\n", 0, span.start) + 1
            right = document.text.find("\n", span.end)
            statements.append(document.text[left:len(document.text) if right < 0 else right].strip())
        normalized["overlap_kind"] = ("none_explicit" if statements and all(
            _EXPLICIT_NO_OVERLAP.fullmatch(statement) for statement in statements) else "uncertain")
    else:
        allowed = {
            "coverage": _ALLOWED_COVERAGE,
            "amount_kind": _ALLOWED_AMOUNT,
            "eligibility_kind": _ALLOWED_ELIGIBILITY,
            "overlap_kind": _ALLOWED_OVERLAP,
            "scope": _ALLOWED_SCOPE,
        }
        for key, choices in allowed.items():
            answer = decisions.get(key)
            if answer in choices:
                normalized[key] = answer
            else:
                _issue(issues, f"{key}_missing")
        if normalized["coverage"] not in {"complete", None}:
            _issue(issues, f"coverage_{normalized['coverage']}")
        if normalized["amount_kind"] not in {"fixed", None}:
            _issue(issues, f"amount_{normalized['amount_kind']}")
        if normalized["eligibility_kind"] not in {"simple", None}:
            _issue(issues, f"eligibility_{normalized['eligibility_kind']}")
        if normalized["overlap_kind"] not in {"none_explicit", None}:
            _issue(issues, f"overlap_{normalized['overlap_kind']}")
        if normalized["scope"] not in {"youth_employment_or_housing", None}:
            _issue(issues, f"scope_{normalized['scope']}")

        consistency = _number(decisions.get("consistent"))
        if consistency is None or not 0 <= consistency <= 1:
            _issue(issues, "consistent_missing")
        else:
            normalized["consistent"] = consistency
            if consistency < _MIN_CONSISTENCY:
                _issue(issues, "consistent_below_threshold")
        readiness = _number(decisions.get("readiness"))
        if readiness is None or not 0 <= readiness <= 2:
            _issue(issues, "readiness_missing")
        else:
            normalized["readiness"] = readiness
            if readiness < _MIN_READINESS:
                _issue(issues, "readiness_below_threshold")

    spans = _valid_spans(extraction, issues)
    normalized["evidence"] = [_evidence_record(span, document) for span in spans]
    fields = _field_values(document.text, issues)
    # Independent source checks: a confident classifier or a narrow locate span
    # must not hide a cap, deduction or exception elsewhere in the source.
    support = fields.get("plcySprtCn")
    if support is not None and normalized["amount_kind"] == "fixed":
        try:
            if _amount_is_ambiguous(support.value, _parse_money(support.value)):
                _issue(issues, "source_amount_not_single_fixed")
        except ValueError:
            _issue(issues, "source_amount_unparseable")
    for name in ("plcySprtCn", "addAplyQlfcCndCn", "ptcpPrpTrgtCn", "etcMttrCn", "earnEtcCn"):
        field = fields.get(name)
        if field is not None:
            text = re.sub(r"예외(?:\s*사항)?\s*없(?:습니다|음)", "", field.value)
            if re.search(r"다만|단\s*[,，]|제외|예외|차감|공제|감액", text):
                _issue(issues, "source_qualifier_requires_review")
            if re.search(r"생애|기\s*수혜|기\s*참여|최대\s*\d+\s*회|\d+\s*회\s*한정", text):
                _issue(issues, "source_lifetime_or_count_limit_unsupported")
            if _OVERLAP_RESTRICTION.search(text):
                _issue(issues, "source_overlap_restriction_present")
    footer = (
        "The 0.95 consistency and 1.95 readiness gates are pre-calibration release "
        "thresholds, not a guarantee of factual truth."
    )
    normalized["threshold_notice"] = (
        "Structured facts are code-read; body candidates remain unreviewed. No model confidence authorizes publication."
        if structured_first else footer)

    title = ""
    summary = ""
    provider = ""
    if target is not None:
        title = target.name
    name_field = fields.get("plcyNm")
    support_field = fields.get("plcySprtCn")
    provider_field = fields.get("rgtrInstCdNm")
    if name_field is None or not name_field.value.strip():
        _issue(issues, "source_field_missing_plcyNm")
    elif target is not None and name_field.value.strip() != title.strip():
        _issue(issues, "policy_name_source_mismatch")
    if support_field is None or not support_field.value.strip():
        _issue(issues, "source_field_missing_plcySprtCn")
    else:
        summary = support_field.value.strip()
    if provider_field is None or not provider_field.value.strip():
        _issue(issues, "source_field_missing_rgtrInstCdNm")
    else:
        provider = provider_field.value.strip()

    amount_spans = _spans(spans, "amount")
    duration_spans = _spans(spans, "duration")
    timing_spans = _spans(spans, "timing")
    payment = None
    if normalized["amount_kind"] in {"fixed", None}:
        payment = _payment_facts(amount_spans, duration_spans, timing_spans, issues, normalized)
    else:
        if amount_spans:
            amount_bodies = [_span_body(span) for span in amount_spans]
            for body in amount_bodies:
                try:
                    mentions = _parse_money(body)
                except ValueError:
                    continue
                if mentions:
                    normalized["amount_won"] = mentions[0].value
                    break

    application = _application(_spans(spans, "application"), fields, issues, normalized)
    _overlap(
        _spans(spans, "overlap"),
        normalized["overlap_kind"],
        issues,
        normalized,
        document.text,
    )
    _exceptions(_spans(spans, "exceptions"), issues, normalized)
    benefit_conditions: list[dict] = []
    executable: list[dict] = []
    if canonical_source is not None:
        benefit_conditions, executable = _parse_eligibility(
            _spans(spans, "eligibility"),
            fields,
            canonical_source,
            issues,
            normalized,
            document.text,
            regions,
        )
    else:
        _issue(issues, "source_binding_invalid")

    if (
        issues
        or payment is None
        or application is None
        or canonical_source is None
        or target is None
        or binding is None
        or external_id is None
    ):
        return Compilation(None, tuple(issues), normalized)

    payload = _payload(
        value,
        title,
        summary,
        provider,
        list(target.categories),
        canonical_source,
        application,
        benefit_conditions,
        executable,
        payment,
        document,
        source_sha256,
        external_id,
        binding.material_sha256,
        issues,
    )
    if payload is None or issues:
        return Compilation(None, tuple(issues), normalized)
    normalized["calculation_ready"] = True
    return Compilation(payload, (), normalized)


def compile_policy(
    value: PolicyAnalysisInput,
    extracted: tuple[DocumentExtraction, ...],
    *,
    regions: Sequence[RegionOption] = (),
) -> Compilation:
    """Compile only after all safety gates pass using the injected official regions."""
    try:
        compilation = _compile(value, extracted, regions=regions)
        candidate = rent_support_candidate(value, extracted)
        if candidate is not None:
            candidate['catalog_record'] = rent_support_record(value, candidate)
            compilation.normalized['executable_rent_support_candidate'] = candidate
        return compilation
    except (AttributeError, ArithmeticError, KeyError, OverflowError, TypeError, ValueError):
        return Compilation(None, ("compilation_failed",), {})
