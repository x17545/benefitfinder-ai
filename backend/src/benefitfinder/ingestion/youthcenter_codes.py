"""Verified decoding for OnTong Youth policy list codes.

The scalar code groups come from the code-definition workbook linked by the official
OnTong Youth Open API page. ``zipCd`` is a comma-separated list of current 5-digit legal
district codes; the region catalog is injected so this module remains independent from
HTTP and storage adapters.

Source evidence:

* Youthcenter Open API catalogue:
  https://www.youthcenter.go.kr/cmnFooter/openapiIntro/oaiDoc/48
* Code definition workbook (last modified 2025-06-30):
  https://www.youthcenter.go.kr/downloadform/API%EC%BD%94%EB%93%9C%EC%A0%95%EB%B3%B4.xlsx
* Administrative standard code source:
  https://www.code.go.kr/stdcode/regCodeL.do

The current legal-code hierarchy includes parent cities whose catalogs may expose
only the parent code: Cheongju 43110, Pohang 47110, and Changwon 48120. Each
official child-code set expands to its parent only when the complete set is
present, or when the source explicitly contains the parent itself.

Known restricted values intentionally return ``CodeState.HOLD``. Unknown values raise
``UnknownYouthcenterCodeError``; they are never inferred as unrestricted.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Literal

from benefitfinder.application.region_options import DistrictOption, RegionOption


YOUTHCENTER_CODE_DEFINITION_URL = (
    "https://www.youthcenter.go.kr/downloadform/"
    "API%EC%BD%94%EB%93%9C%EC%A0%95%EB%B3%B4.xlsx"
)
YOUTHCENTER_CODE_DEFINITION_VERSION = "2025-06-30"
YOUTHCENTER_CODE_DEFINITION_SHA256 = (
    "81cd89ddc7bd49dfa9e53dec4f093bc8372d241505b5e8374cbfaf018245a5ef"
)
LEGAL_REGION_CODE_SOURCE_URL = "https://www.code.go.kr/stdcode/regCodeL.do"
LEGAL_REGION_CODE_VERSION = "2026-10-01"
LEGAL_REGION_CODE_ARCHIVE_SHA256 = (
    "44a3fe4cacf88ce98e35fb883807d2725b589f427ff4d73ee40b914d1286bacd"
)

CodeField = Literal[
    "jobCd",
    "schoolCd",
    "earnCndSeCd",
    "mrgSttsCd",
    "sbizCd",
    "plcyPvsnMthdCd",
    "plcyMajorCd",
]


class CodeState(str, Enum):
    """How a decoded value may be used by downstream eligibility code."""

    MISSING = "missing"
    UNRESTRICTED = "unrestricted"
    HOLD = "hold"
    MAPPED = "mapped"


@dataclass(frozen=True)
class CodeDefinition:
    label: str
    normalized: str | None
    unrestricted: bool = False


@dataclass(frozen=True)
class DecodedCode:
    """One official scalar code with both its source value and verified meaning."""

    field: CodeField
    raw: str | None
    code: str | None
    label: str | None
    normalized: str | None
    state: CodeState

    @property
    def holds(self) -> bool:
        return self.state is CodeState.HOLD


@dataclass(frozen=True)
class DecodedCodeList:
    """Ordered verified definitions from one comma-separated source field."""

    field: CodeField
    raw: str | None
    codes: tuple[DecodedCode, ...]
    state: CodeState

    @property
    def holds(self) -> bool:
        return self.state is CodeState.HOLD

    @property
    def contradictory(self) -> bool:
        states = {code.state for code in self.codes}
        return CodeState.UNRESTRICTED in states and CodeState.HOLD in states


@dataclass(frozen=True)
class ZipRegionGroup:
    """A maximal verified region group represented by one or more source codes."""

    code: str
    name: str
    source_codes: tuple[str, ...]


@dataclass(frozen=True)
class ZipCoverage:
    """Decoded policy residence coverage while retaining the exact source string."""

    raw: str | None
    codes: tuple[str, ...]
    groups: tuple[ZipRegionGroup, ...]
    nationwide: bool
    state: CodeState

    @property
    def text(self) -> str | None:
        if self.state is CodeState.MISSING:
            return None
        return ", ".join(group.name for group in self.groups)

    @property
    def holds(self) -> bool:
        return self.state is CodeState.HOLD


class YouthcenterCodeError(ValueError):
    """Base error for values that cannot be used as verified policy-code meanings."""


class InvalidYouthcenterCodeError(YouthcenterCodeError):
    pass


class UnknownYouthcenterCodeError(YouthcenterCodeError):
    def __init__(self, field: str, code: str) -> None:
        self.field = field
        self.code = code
        super().__init__(f"{field}에 알 수 없는 코드가 있습니다: {code}")


class InvalidRegionCatalogError(ValueError):
    pass


class IncompleteRegionAliasError(InvalidYouthcenterCodeError):
    """An official child-code group is missing one or more required children."""


# Source: LEGAL_REGION_CODE_SOURCE_URL at LEGAL_REGION_CODE_VERSION (2026-10-01).
_CITY_DISTRICT_ALIASES: Mapping[str, tuple[tuple[str, str], ...]] = MappingProxyType(
    {
        "43110": (
            ("43111", "상당구"),
            ("43112", "서원구"),
            ("43113", "흥덕구"),
            ("43114", "청원구"),
        ),
        "47110": (
            ("47111", "남구"),
            ("47113", "북구"),
        ),
        "48120": (
            ("48121", "의창구"),
            ("48123", "성산구"),
            ("48125", "마산합포구"),
            ("48127", "마산회원구"),
            ("48129", "진해구"),
        ),
    }
)


JOB_CODES: Mapping[str, CodeDefinition] = MappingProxyType(
    {
        "0013001": CodeDefinition("재직자", "employed"),
        "0013002": CodeDefinition("자영업자", "self_employed"),
        "0013003": CodeDefinition("미취업자", "job_seeker"),
        "0013004": CodeDefinition("프리랜서", "freelancer"),
        "0013005": CodeDefinition("일용근로자", "daily_worker"),
        "0013006": CodeDefinition("(예비)창업자", "founder"),
        "0013007": CodeDefinition("단기근로자", "short_term_worker"),
        "0013008": CodeDefinition("영농종사자", "farmer"),
        "0013009": CodeDefinition("기타", "other"),
        "0013010": CodeDefinition("제한없음", "unrestricted", unrestricted=True),
    }
)

SCHOOL_CODES: Mapping[str, CodeDefinition] = MappingProxyType(
    {
        "0049001": CodeDefinition("고졸 미만", "high_school_below"),
        "0049002": CodeDefinition("고교 재학", "high_school_enrolled"),
        "0049003": CodeDefinition("고졸 예정", None),
        "0049004": CodeDefinition("고교 졸업", "high_school_graduate"),
        "0049005": CodeDefinition("대학 재학", "university_enrolled"),
        "0049006": CodeDefinition("대졸 예정", "university_expected"),
        "0049007": CodeDefinition("대학 졸업", "university_graduate"),
        "0049008": CodeDefinition("석·박사", "masters_doctorate"),
        "0049009": CodeDefinition("기타", None),
        "0049010": CodeDefinition("제한없음", None, unrestricted=True),
    }
)

EARN_CONDITION_CODES: Mapping[str, CodeDefinition] = MappingProxyType(
    {
        "0043001": CodeDefinition("무관", "no_income_condition", unrestricted=True),
        "0043002": CodeDefinition("연소득", "annual_income"),
        "0043003": CodeDefinition("기타", "other"),
    }
)

MARITAL_STATUS_CODES: Mapping[str, CodeDefinition] = MappingProxyType(
    {
        "0055001": CodeDefinition("기혼", "married"),
        "0055002": CodeDefinition("미혼", "single"),
        "0055003": CodeDefinition("제한없음", "unrestricted", unrestricted=True),
    }
)

SPECIAL_CONDITION_CODES: Mapping[str, CodeDefinition] = MappingProxyType(
    {
        "0014001": CodeDefinition("중소기업", "sme"),
        "0014002": CodeDefinition("여성", "women"),
        "0014003": CodeDefinition("기초생활수급자", "basic_livelihood"),
        "0014004": CodeDefinition("한부모가정", "single_parent"),
        "0014005": CodeDefinition("장애인", "disabled"),
        "0014006": CodeDefinition("농업인", "farmer"),
        "0014007": CodeDefinition("군인", "soldier"),
        "0014008": CodeDefinition("지역인재", "local_talent"),
        "0014009": CodeDefinition("기타", "other"),
        "0014010": CodeDefinition("제한없음", "unrestricted", unrestricted=True),
    }
)

MAJOR_CODES: Mapping[str, CodeDefinition] = MappingProxyType(
    {
        "0011001": CodeDefinition("인문계열", "humanities"),
        "0011002": CodeDefinition("사회계열", "social_sciences"),
        "0011003": CodeDefinition("상경계열", "business_economics"),
        "0011004": CodeDefinition("이학계열", "natural_sciences"),
        "0011005": CodeDefinition("공학계열", "engineering"),
        "0011006": CodeDefinition("예체능계열", "arts_sports"),
        "0011007": CodeDefinition("농산업계열", "agriculture"),
        "0011008": CodeDefinition("기타", "other"),
        "0011009": CodeDefinition("제한없음", "unrestricted", unrestricted=True),
    }
)

PROVISION_METHOD_CODES: Mapping[str, CodeDefinition] = MappingProxyType(
    {
        "0042001": CodeDefinition("인프라 구축", "infrastructure"),
        "0042002": CodeDefinition("프로그램", "program"),
        "0042003": CodeDefinition("직접대출", "direct_loan"),
        "0042004": CodeDefinition("공공기관", "public_institution"),
        "0042005": CodeDefinition("계약(위탁운영)", "contracted_operation"),
        "0042006": CodeDefinition("보조금", "subsidy"),
        "0042007": CodeDefinition("대출보증", "loan_guarantee"),
        "0042008": CodeDefinition("공적보험", "public_insurance"),
        "0042009": CodeDefinition("조세지출", "tax_expenditure"),
        "0042010": CodeDefinition("바우처", "voucher"),
        "0042011": CodeDefinition("정보제공", "information"),
        "0042012": CodeDefinition("경제적 규제", "economic_regulation"),
        "0042013": CodeDefinition("기타", "other"),
    }
)

CODE_GROUPS: Mapping[CodeField, str] = MappingProxyType(
    {
        "jobCd": "0013",
        "plcyMajorCd": "0011",
        "schoolCd": "0049",
        "earnCndSeCd": "0043",
        "mrgSttsCd": "0055",
        "sbizCd": "0014",
        "plcyPvsnMthdCd": "0042",
    }
)

_TABLES: Mapping[CodeField, Mapping[str, CodeDefinition]] = MappingProxyType(
    {
        "jobCd": JOB_CODES,
        "plcyMajorCd": MAJOR_CODES,
        "schoolCd": SCHOOL_CODES,
        "earnCndSeCd": EARN_CONDITION_CODES,
        "mrgSttsCd": MARITAL_STATUS_CODES,
        "sbizCd": SPECIAL_CONDITION_CODES,
        "plcyPvsnMthdCd": PROVISION_METHOD_CODES,
    }
)
_REQUIREMENT_FIELDS = frozenset(_TABLES) - {"plcyPvsnMthdCd"}


def decode_code(field: CodeField, value: str | None) -> DecodedCode:
    """Decode one scalar API code or fail on an unknown/combined value.

    Empty/whitespace values are explicitly missing. The API's scalar fields are not
    treated as comma-separated values. ``plcyPvsnMthdCd`` is descriptive metadata and
    therefore returns ``MAPPED``; other supported fields return ``HOLD`` when they carry
    a verified restriction, which must remain visible to the calculator.
    """
    table = _TABLES.get(field)
    if table is None:
        raise ValueError(f"지원하지 않는 코드 필드입니다: {field}")
    if value is not None and not isinstance(value, str):
        raise TypeError(f"{field} 코드는 문자열이어야 합니다.")
    if value is None or not value.strip():
        return DecodedCode(field, value, None, None, None, CodeState.MISSING)

    code = value.strip()
    definition = table.get(code)
    if definition is None:
        raise UnknownYouthcenterCodeError(field, code)
    if field not in _REQUIREMENT_FIELDS:
        state = CodeState.MAPPED
    elif definition.unrestricted:
        state = CodeState.UNRESTRICTED
    else:
        state = CodeState.HOLD
    return DecodedCode(
        field=field,
        raw=value,
        code=code,
        label=definition.label,
        normalized=definition.normalized,
        state=state,
    )


def decode_code_list(field: CodeField, value: str | None) -> DecodedCodeList:
    """Decode every item from a comma-separated API code field.

    Each item retains its own official definition. A list mixing an unrestricted marker
    with a restricted value is contradictory and returns ``HOLD``. Unknown items are never
    ignored; the whole decode fails with ``UnknownYouthcenterCodeError``.
    """
    if field not in _TABLES:
        raise ValueError(f"지원하지 않는 코드 필드입니다: {field}")
    if value is not None and not isinstance(value, str):
        raise TypeError(f"{field} 코드는 문자열이어야 합니다.")
    if value is None or not value.strip():
        return DecodedCodeList(field, value, (), CodeState.MISSING)

    parts = tuple(part.strip() for part in value.split(","))
    if any(not part for part in parts):
        raise InvalidYouthcenterCodeError(f"{field}에 빈 코드 항목이 있습니다.")
    codes = tuple(decode_code(field, part) for part in parts)
    states = {code.state for code in codes}
    if field not in _REQUIREMENT_FIELDS:
        state = CodeState.MAPPED
    elif CodeState.UNRESTRICTED in states and CodeState.HOLD in states:
        state = CodeState.HOLD
    elif CodeState.UNRESTRICTED in states:
        state = CodeState.UNRESTRICTED
    else:
        state = CodeState.HOLD
    return DecodedCodeList(field, value, codes, state)


@dataclass(frozen=True)
class _RegionUnit:
    code: str
    name: str
    children: tuple[str, ...]


@dataclass(frozen=True)
class _ProvinceCatalog:
    code: str
    name: str
    marker: str
    units: tuple[_RegionUnit, ...]
    codes: frozenset[str]
    names: Mapping[str, str]
    aliases: Mapping[str, tuple[str, ...]]


def _province_marker(sido_code: str) -> str:
    # The official source has no 3600000000 row; Sejong's sole lower code is 36110.
    return "36110" if sido_code == "36" else sido_code + "000"


def _display_name(province: str, district: str) -> str:
    if district == province or district.startswith(province + " "):
        return district
    return f"{province} {district}"


def _region_catalog(
    regions: Sequence[RegionOption],
) -> tuple[tuple[_ProvinceCatalog, ...], frozenset[str]]:
    if not regions:
        raise InvalidRegionCatalogError("공식 지역 목록이 비어 있습니다.")

    province_codes: set[str] = set()
    district_codes: set[str] = set()
    catalogs: list[_ProvinceCatalog] = []
    for region in regions:
        if not re.fullmatch(r"[0-9]{2}", region.sido_code):
            raise InvalidRegionCatalogError("시도 코드가 두 자리 숫자가 아닙니다.")
        if region.sido_code in province_codes:
            raise InvalidRegionCatalogError("시도 코드가 중복되었습니다.")
        if not region.sido_name.strip() or not region.sigungu:
            raise InvalidRegionCatalogError("시도명 또는 시군구 목록이 비어 있습니다.")
        province_codes.add(region.sido_code)

        by_code: dict[str, DistrictOption] = {}
        for district in region.sigungu:
            if not re.fullmatch(r"[0-9]{5}", district.code):
                raise InvalidRegionCatalogError("시군구 코드가 다섯 자리 숫자가 아닙니다.")
            if not district.code.startswith(region.sido_code):
                raise InvalidRegionCatalogError("시군구 코드가 시도 코드와 맞지 않습니다.")
            if district.code in district_codes:
                raise InvalidRegionCatalogError("시군구 코드가 중복되었습니다.")
            if not district.name.strip():
                raise InvalidRegionCatalogError("시군구명이 비어 있습니다.")
            district_codes.add(district.code)
            by_code[district.code] = district

        aliases: dict[str, tuple[str, ...]] = {}
        for parent_code, children in _CITY_DISTRICT_ALIASES.items():
            parent = by_code.get(parent_code)
            if parent is None or any(code in by_code for code, _ in children):
                continue
            added: list[str] = []
            for code, child_name in children:
                if code in district_codes:
                    raise InvalidRegionCatalogError("지역 별칭 코드가 중복되었습니다.")
                name = (
                    child_name
                    if child_name.startswith(parent.name.strip())
                    else f"{parent.name.strip()} {child_name}"
                )
                by_code[code] = DistrictOption(code, name)
                district_codes.add(code)
                added.append(code)
            aliases[parent_code] = tuple(added)

        child_to_parent: dict[str, str] = {}
        for code in by_code:
            parent = code[:4] + "0"
            if parent != code and parent in by_code:
                child_to_parent[code] = parent

        children_by_parent: dict[str, list[str]] = {}
        for child, parent in child_to_parent.items():
            children_by_parent.setdefault(parent, []).append(child)

        units = tuple(
            _RegionUnit(
                code=district.code,
                name=district.name,
                children=tuple(sorted(children_by_parent.get(district.code, ()))),
            )
            for district in region.sigungu
            if district.code not in child_to_parent
        )
        if not units:
            raise InvalidRegionCatalogError("시군구 계층에 최상위 지역이 없습니다.")
        marker = _province_marker(region.sido_code)
        catalogs.append(
            _ProvinceCatalog(
                code=region.sido_code,
                name=region.sido_name,
                marker=marker,
                units=units,
                codes=frozenset((marker, *by_code)),
                names=MappingProxyType(
                    {code: district.name for code, district in by_code.items()}
                ),
                aliases=MappingProxyType(aliases),
            )
        )

    known = frozenset(code for catalog in catalogs for code in catalog.codes)
    if len(known) != sum(len(catalog.codes) for catalog in catalogs):
        raise InvalidRegionCatalogError("시도 코드가 시군구 코드와 중복되었습니다.")
    return tuple(catalogs), known


def _covered(unit: _RegionUnit, selected: set[str]) -> bool:
    if unit.code in selected:
        return True
    return bool(unit.children) and all(child in selected for child in unit.children)


def decode_zip_codes(
    value: str | None,
    regions: Sequence[RegionOption],
) -> ZipCoverage:
    """Decode ``zipCd`` using a complete, current official region catalog.

    A province is complete only when every top-level unit in the supplied catalog is
    covered. A parent municipality with general districts is covered only by selecting the
    parent itself or every catalogued child district. Nationwide is reserved for complete
    coverage of every supplied province. All other known coverage is ``HOLD`` so a policy
    region restriction remains available as a calculator selection condition.

    Unknown and obsolete codes fail with ``UnknownYouthcenterCodeError``. The caller must
    preserve the returned ``raw`` value alongside the normalized ``text``.
    """
    if value is not None and not isinstance(value, str):
        raise TypeError("zipCd는 문자열이어야 합니다.")
    if value is None or not value.strip():
        return ZipCoverage(value, (), (), False, CodeState.MISSING)

    parts = tuple(part.strip() for part in value.split(","))
    if any(not re.fullmatch(r"[0-9]{5}", part) for part in parts):
        raise InvalidYouthcenterCodeError("zipCd에 다섯 자리 숫자가 아닌 항목이 있습니다.")
    codes = tuple(dict.fromkeys(parts))

    catalogs, known = _region_catalog(regions)
    for code in codes:
        if code not in known:
            raise UnknownYouthcenterCodeError("zipCd", code)

    selected = set(codes)
    for catalog in catalogs:
        for parent_code, children in catalog.aliases.items():
            present = tuple(code for code in children if code in selected)
            if not present:
                continue
            if len(present) != len(children) and parent_code not in selected:
                raise IncompleteRegionAliasError(
                    f"zipCd에 {parent_code}의 하위 지역 일부만 있어 "
                    "상위 지역 전체로 해석할 수 없습니다."
                )
            selected.difference_update(children)
            if parent_code in selected or len(present) == len(children):
                selected.add(parent_code)

    complete_provinces = tuple(
        catalog
        for catalog in catalogs
        if catalog.marker in selected
        or all(_covered(unit, selected) for unit in catalog.units)
    )
    if len(complete_provinces) == len(catalogs):
        group = ZipRegionGroup("*", "전국", codes)
        return ZipCoverage(value, codes, (group,), True, CodeState.UNRESTRICTED)

    complete_codes = {catalog.code for catalog in complete_provinces}
    groups: list[ZipRegionGroup] = []
    for catalog in catalogs:
        province_sources = tuple(code for code in codes if code in catalog.codes)
        if catalog.code in complete_codes:
            groups.append(ZipRegionGroup(catalog.marker, catalog.name, province_sources))
            continue

        for unit in catalog.units:
            unit_codes = frozenset((unit.code, *unit.children))
            if _covered(unit, selected):
                groups.append(
                    ZipRegionGroup(
                        unit.code,
                        _display_name(catalog.name, unit.name),
                        tuple(code for code in codes if code in unit_codes),
                    )
                )
                continue
            for child in unit.children:
                if child in selected:
                    groups.append(
                        ZipRegionGroup(
                            child,
                            _display_name(catalog.name, catalog.names[child]),
                            (child,),
                        )
                    )

    return ZipCoverage(value, codes, tuple(groups), False, CodeState.HOLD)


__all__ = [
    "CODE_GROUPS",
    "CodeField",
    "CodeDefinition",
    "CodeState",
    "DecodedCode",
    "DecodedCodeList",
    "EARN_CONDITION_CODES",
    "InvalidRegionCatalogError",
    "InvalidYouthcenterCodeError",
    "JOB_CODES",
    "LEGAL_REGION_CODE_ARCHIVE_SHA256",
    "LEGAL_REGION_CODE_SOURCE_URL",
    "LEGAL_REGION_CODE_VERSION",
    "MARITAL_STATUS_CODES",
    "PROVISION_METHOD_CODES",
    "SCHOOL_CODES",
    "SPECIAL_CONDITION_CODES",
    "UnknownYouthcenterCodeError",
    "YOUTHCENTER_CODE_DEFINITION_SHA256",
    "YOUTHCENTER_CODE_DEFINITION_URL",
    "YOUTHCENTER_CODE_DEFINITION_VERSION",
    "YouthcenterCodeError",
    "ZipCoverage",
    "ZipRegionGroup",
    "decode_code",
    "decode_code_list",
    "decode_zip_codes",
]
