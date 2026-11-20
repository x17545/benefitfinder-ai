"""Compile verified structured OnTong Youth fields into catalogue eligibility.

The compiler is deliberately source-bound and offline. It turns only the scalar
age, job, school, marital-status, and residence fields whose meanings are defined
by the official code workbook into executable rules. Every non-empty source field
that is not safely consumed remains a manual condition with exact evidence offsets.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import re

from benefitfinder.application.policy_analysis import PolicyAnalysisInput
from benefitfinder.application.policy_rules import SourceDocument
from benefitfinder.application.region_options import RegionOption
from benefitfinder.ingestion.youthcenter_codes import (
    CodeField,
    CodeState,
    InvalidRegionCatalogError,
    YouthcenterCodeError,
    decode_code_list,
    decode_zip_codes,
)


_HEADER = re.compile(r"(?m)^\[([A-Za-z][A-Za-z0-9]{0,99})\]\r?\n")
_MAX_EVIDENCE_QUOTE = 2500
_MAX_CONDITIONS = 100

_JOB_VALUES = frozenset(
    {
        "employed",
        "self_employed",
        "job_seeker",
        "freelancer",
        "short_term_worker",
        "founder",
        "farmer",
        "other",
    }
)
_SCHOOL_VALUES = frozenset(
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
_MARITAL_VALUES = frozenset({"single", "married"})

_LABELS = {
    "plcyNm": "정책명",
    "plcyNo": "정책 번호",
    "plcyExplnCn": "정책 설명",
    "plcySprtCn": "지원 내용",
    "plcyKywdNm": "정책 키워드",
    "plcyAplyMthdCn": "신청 방법",
    "plcyPvsnMthdCd": "지원 방식 코드",
    "plcyMajorCd": "전공 코드",
    "addAplyQlfcCndCn": "추가 신청 자격",
    "ptcpPrpTrgtCn": "참여 제한 대상",
    "etcMttrCn": "기타 사항",
    "aplyYmd": "신청 기간",
    "aplyPrdSeCd": "신청 기간 구분 코드",
    "aplyUrlAddr": "신청 URL",
    "bizPrdBgngYmd": "사업 시작일",
    "bizPrdEndYmd": "사업 종료일",
    "bizPrdEtcCn": "사업 기간 기타 사항",
    "bizPrdSeCd": "사업 기간 구분 코드",
    "srngMthdCn": "모집 방법",
    "sbmsnDcmntCn": "제출 서류",
    "earnCndSeCd": "소득 조건 코드",
    "earnMinAmt": "소득 하한",
    "earnMaxAmt": "소득 상한",
    "earnEtcCn": "기타 소득 조건",
    "sprtTrgtAgeLmtYn": "연령 제한 여부",
    "sprtTrgtMinAge": "최소 연령",
    "sprtTrgtMaxAge": "최대 연령",
    "schoolCd": "학력 코드",
    "jobCd": "취업 상태 코드",
    "mrgSttsCd": "혼인 상태 코드",
    "sbizCd": "특화 조건 코드",
    "zipCd": "지역 코드",
    "refUrlAddr1": "참고 URL 1",
    "refUrlAddr2": "참고 URL 2",
    "lclsfNm": "대분류",
    "mclsfNm": "중분류",
    "rgtrInstCdNm": "등록 기관",
    "sprvsnInstCdNm": "주관 기관",
    "operInstCdNm": "운영 기관",
    "sprtSclCnt": "지원 규모",
    "sprtSclLmtYn": "지원 규모 제한 여부",
    "sprtArvlSeqYn": "선착순 여부",
}


@dataclass(frozen=True)
class _Field:
    name: str
    value: str
    start: int
    end: int
    occurrence: int
    occurrences: int

    @property
    def order(self) -> int:
        return self.start


@dataclass(frozen=True)
class _Executable:
    condition: dict[str, object] | None
    rule: dict[str, object] | None
    fields: tuple[_Field, ...]


@dataclass(frozen=True)
class _ConditionEntry:
    order: int
    condition: dict[str, object]
    rule: dict[str, object]


def _strip_projection_separator(value: str) -> str:
    for separator in ("\r\n\r\n", "\n\n"):
        if value.endswith(separator):
            return value[: -len(separator)]
    for separator in ("\r\n", "\n"):
        if value.endswith(separator):
            return value[: -len(separator)]
    return value


def _fields(text: str) -> tuple[_Field, ...]:
    headers = list(_HEADER.finditer(text))
    if not headers or text[: headers[0].start()].strip():
        raise ValueError("source_projection_invalid")

    raw_fields: list[tuple[str, str, int, int]] = []
    for index, header in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        value = _strip_projection_separator(text[header.end() : end])
        raw_fields.append((header.group(1), value, header.end(), header.end() + len(value)))

    totals: dict[str, int] = {}
    seen: dict[str, int] = {}
    for name, _, _, _ in raw_fields:
        totals[name] = totals.get(name, 0) + 1

    fields: list[_Field] = []
    for name, value, start, end in raw_fields:
        seen[name] = seen.get(name, 0) + 1
        fields.append(_Field(name, value, start, end, seen[name], totals[name]))
    return tuple(fields)


def _source(document: SourceDocument) -> dict[str, str]:
    return {
        "url": document.source_url,
        "document_version": document.document_version,
        "checked_at": document.collected_at.isoformat(),
    }


def _evidence(
    fields: Sequence[_Field],
    source: dict[str, str],
) -> list[dict[str, object]]:
    evidence: list[dict[str, object]] = []
    for field in sorted(fields, key=lambda item: item.start):
        for offset in range(0, len(field.value), _MAX_EVIDENCE_QUOTE):
            quote = field.value[offset : offset + _MAX_EVIDENCE_QUOTE]
            start = field.start + offset
            evidence.append(
                {
                    "quote": quote,
                    "location": f"chars:{start}-{start + len(quote)}",
                    "source": source.copy(),
                }
            )
    return evidence


def _only(fields_by_name: dict[str, list[_Field]], name: str) -> _Field | None:
    fields = fields_by_name.get(name, [])
    if len(fields) != 1 or not fields[0].value.strip():
        return None
    return fields[0]


def _compile_age(fields_by_name: dict[str, list[_Field]]) -> _Executable | None:
    flag = _only(fields_by_name, "sprtTrgtAgeLmtYn")
    minimum_field = _only(fields_by_name, "sprtTrgtMinAge")
    maximum_field = _only(fields_by_name, "sprtTrgtMaxAge")
    if flag is None:
        return None

    marker = flag.value.strip().upper()
    if marker == "Y":
        if (
            (minimum_field is not None and minimum_field.value.strip())
            or (maximum_field is not None and maximum_field.value.strip())
        ):
            return None
        return _Executable(None, None, (flag,))
    if marker != "N" or minimum_field is None or maximum_field is None:
        return None

    try:
        minimum = int(minimum_field.value.strip())
        maximum = int(maximum_field.value.strip())
    except ValueError:
        return None
    if not 0 <= minimum <= maximum <= 120 or minimum == maximum == 0:
        return None

    condition_id = "age"
    return _Executable(
        {
            "id": condition_id,
            "description": f"만 {minimum}세 이상 {maximum}세 이하",
            "evidence": [],
        },
        {
            "condition_id": condition_id,
            "kind": "age",
            "minimum": minimum,
            "maximum": maximum,
            "military_extension_years": 0,
        },
        (flag, minimum_field, maximum_field),
    )


def _compile_code_field(
    fields_by_name: dict[str, list[_Field]],
    *,
    source_field: CodeField,
    target_field: str,
    allowed_values: frozenset[str],
    condition_id: str,
) -> _Executable | None:
    field = _only(fields_by_name, source_field)
    if field is None:
        return None
    try:
        decoded = decode_code_list(source_field, field.value)
    except (YouthcenterCodeError, TypeError, ValueError):
        return None
    if decoded.contradictory:
        return None
    if decoded.state is CodeState.UNRESTRICTED:
        return _Executable(None, None, (field,))
    if not decoded.codes:
        return None

    normalized: list[str] = []
    labels: list[str] = []
    for code in decoded.codes:
        if code.normalized is None or code.normalized not in allowed_values:
            return None
        if code.normalized not in normalized:
            normalized.append(code.normalized)
        labels.append(f"{code.code}({code.label})")
    if not normalized:
        return None

    return _Executable(
        {
            "id": condition_id,
            "description": f"{source_field}: {', '.join(labels)}",
            "evidence": [],
        },
        {
            "condition_id": condition_id,
            "kind": "selection",
            "field": target_field,
            "values": normalized,
        },
        (field,),
    )


def _province_marker(sido_code: str) -> str:
    return "36110" if sido_code == "36" else sido_code + "000"


def _compile_region(
    fields_by_name: dict[str, list[_Field]],
    regions: Sequence[RegionOption],
) -> _Executable | None:
    field = _only(fields_by_name, "zipCd")
    if field is None:
        return None
    try:
        coverage = decode_zip_codes(field.value, regions)
    except (InvalidRegionCatalogError, YouthcenterCodeError, TypeError, ValueError):
        return None
    if coverage.nationwide:
        return _Executable(None, None, (field,))
    if not coverage.groups:
        return None

    markers = {_province_marker(region.sido_code): region for region in regions}
    targets: set[str] = set()
    values: list[str] = []
    for group in coverage.groups:
        province = markers.get(group.code)
        if province is None:
            targets.add("region_code")
            candidate = group.code
        else:
            targets.add("domain_data.region.sido_code")
            candidate = province.sido_code
        if candidate not in values:
            values.append(candidate)
    if len(targets) != 1 or not values:
        return None

    target = next(iter(targets))
    condition_id = (
        "selection_region_code"
        if target == "region_code"
        else "selection_domain_data_region_sido_code"
    )
    return _Executable(
        {
            "id": condition_id,
            "description": "zipCd: " + ", ".join(group.name for group in coverage.groups),
            "evidence": [],
        },
        {
            "condition_id": condition_id,
            "kind": "selection",
            "field": target,
            "values": values,
        },
        (field,),
    )


def _manual_id(field: _Field) -> str:
    condition_id = "source:" + field.name
    if field.occurrences > 1:
        condition_id += f":{field.occurrence}"
    return condition_id


def _manual_description(field: _Field) -> str:
    return f"{_LABELS.get(field.name, field.name)}: {field.value}"


def compile_source_eligibility(
    value: PolicyAnalysisInput,
    *,
    regions: Sequence[RegionOption] = (),
) -> tuple[list[dict], list[dict]]:
    """Compile one OnTong Youth projection into conditions and executable rules.

    The returned lists are ordered identically and have the same condition IDs.
    Unsupported, unknown, mixed, or duplicated structured fields remain manual;
    they are not promoted to an empty or broad executable rule. Application
    status and date semantics are left to the caller.
    """

    if len(value.documents) != 1:
        raise ValueError("source_eligibility_single_document_required")
    document = value.documents[0]
    if not document.official:
        raise ValueError("source_eligibility_official_document_required")

    parsed = _fields(document.text)
    fields_by_name: dict[str, list[_Field]] = {}
    for field in parsed:
        fields_by_name.setdefault(field.name, []).append(field)

    source = _source(document)
    consumed: set[int] = set()
    entries: list[_ConditionEntry] = []

    executable_candidates = (
        _compile_age(fields_by_name),
        _compile_code_field(
            fields_by_name,
            source_field="jobCd",
            target_field="employment_status",
            allowed_values=_JOB_VALUES,
            condition_id="selection_employment_status",
        ),
        _compile_code_field(
            fields_by_name,
            source_field="schoolCd",
            target_field="domain_data.school",
            allowed_values=_SCHOOL_VALUES,
            condition_id="selection_domain_data_school",
        ),
        _compile_code_field(
            fields_by_name,
            source_field="mrgSttsCd",
            target_field="domain_data.marital",
            allowed_values=_MARITAL_VALUES,
            condition_id="selection_domain_data_marital",
        ),
        _compile_region(fields_by_name, regions),
    )

    for candidate in executable_candidates:
        if candidate is None:
            continue
        fields = candidate.fields
        consumed.update(field.start for field in fields)
        if candidate.condition is None or candidate.rule is None:
            continue
        condition = candidate.condition.copy()
        condition["evidence"] = _evidence(fields, source)
        entries.append(
            _ConditionEntry(
                min(field.order for field in fields),
                condition,
                candidate.rule,
            )
        )

    for field in parsed:
        if field.start in consumed or not field.value.strip():
            continue
        condition_id = _manual_id(field)
        entries.append(
            _ConditionEntry(
                field.order,
                {
                    "id": condition_id,
                    "description": _manual_description(field),
                    "evidence": _evidence((field,), source),
                },
                {"condition_id": condition_id, "kind": "manual"},
            )
        )

    entries.sort(key=lambda item: item.order)
    if len(entries) > _MAX_CONDITIONS:
        raise ValueError("source_eligibility_condition_limit_exceeded")

    conditions = [entry.condition for entry in entries]
    rules = [entry.rule for entry in entries]
    if len({condition["id"] for condition in conditions}) != len(conditions):
        raise ValueError("source_eligibility_duplicate_condition_id")
    return conditions, rules


def projection_values(text: str) -> dict[str, str]:
    """Return fields that appear exactly once in one OnTong Youth projection."""

    fields_by_name: dict[str, list[_Field]] = {}
    for field in _fields(text):
        fields_by_name.setdefault(field.name, []).append(field)
    return {
        name: fields[0].value
        for name, fields in fields_by_name.items()
        if len(fields) == 1
    }


def compile_source_restrictions(
    text: str,
    source: dict[str, str],
    *,
    regions: Sequence[RegionOption] = (),
) -> tuple[list[dict], list[dict]]:
    """Compile only the executable age and residence rules of one projection.

    This is the subset that a separately written record (for example a service
    record) can add without replacing its own conditions. Nationwide, unknown,
    mixed, or partial coverage returns no rule, exactly as in
    ``compile_source_eligibility``. Evidence offsets point into ``text``.
    """

    fields_by_name: dict[str, list[_Field]] = {}
    for field in _fields(text):
        fields_by_name.setdefault(field.name, []).append(field)

    conditions: list[dict] = []
    rules: list[dict] = []
    for candidate in (_compile_age(fields_by_name), _compile_region(fields_by_name, regions)):
        if candidate is None or candidate.condition is None or candidate.rule is None:
            continue
        condition = candidate.condition.copy()
        condition["evidence"] = _evidence(candidate.fields, source)
        conditions.append(condition)
        rules.append(dict(candidate.rule))
    return conditions, rules
