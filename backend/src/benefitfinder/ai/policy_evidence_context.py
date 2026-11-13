"""Structural source-field context without rewriting or interpreting policy text."""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class ApiFieldRange:
    name: str
    start_line: int
    end_line: int
    kind: str


_FIELD_HEADER = re.compile(r"^\s*\[([A-Za-z][A-Za-z0-9_]*)\]\s*$")
_FIELD_GROUPS = (
    (
        "eligibility",
        frozenset(
            {
                "sprtTrgtAgeLmtYn",
                "sprtTrgtMinAge",
                "sprtTrgtMaxAge",
                "ageStart",
                "ageEnd",
                "earnCndSeCd",
                "earnMinAmt",
                "earnMaxAmt",
                "earnEtcCn",
                "addAplyQlfcCndCn",
                "ptcpPrpTrgtCn",
                "jobCd",
                "schoolCd",
                "mrgSttsCd",
                "zipCd",
                "plcyMajorCd",
                "detailTarget",
                "detailCriteria",
            }
        ),
    ),
    (
        "application",
        frozenset(
            {
                "plcyAplyMthdCn",
                "aplyYmd",
                "aplyPrdSeCd",
                "srngMthdCn",
                "sbmsnDcmntCn",
                "detailMethod",
                "detailDeadline",
                "detailDocuments",
            }
        ),
    ),
    (
        "program_period",
        frozenset({"bizPrdBgngYmd", "bizPrdEndYmd", "bizPrdEtcCn", "bizPrdSeCd"}),
    ),
    ("support", frozenset({"plcySprtCn", "detailSupport"})),
    ("mixed", frozenset({"plcyExplnCn", "detailPurpose", "etcMttrCn"})),
)
_FIELD_KINDS = {
    name: kind
    for kind, names in _FIELD_GROUPS
    for name in names
}
_EVIDENCE_KINDS = frozenset(
    {
        "eligibility",
        "amount",
        "timing",
        "application",
        "service",
        "exceptions",
        "program_period",
        "source_context",
    }
)

_TRANSACTION_AMOUNT_LABEL = re.compile(
    r"거래\s*금액(?:\s*(?:산식|계산식|산정식|산정))?\s*(?:\([^)]*\))?\s*$"
)
_FORMULA_RELATION = re.compile(r"(?:=|＝|:\s*|：\s*)")
_ARITHMETIC = re.compile(r"[+\-−－×＊*/÷]")
_FORMULA_TERM = re.compile(
    r"주택가격|매매가격|분양가|공시가격|보증금|월세|전세|임대료|자가|시세"
)
_FORMULA_REFERENCE = re.compile(
    r"(?:위|해당|이|같은)\s*(?:산식|수식|계산식|공식)"
    r"|(?:산식|수식|계산식|공식)\s*(?:에서|에\s*따라|기준)"
)
_CONDITION_CUE = re.compile(
    r"단\s*[,，:]|다만|이\s*경우|이때|여기서|예외|한도|조건|제외|포함|"
    r"가산|차감|이상|이하|미만|초과|이내|경우|또는|(?<![A-Za-z])OR(?![A-Za-z])"
)
_LIST_PREFIX = re.compile(
    r"^(?:[-•·※*]|\(?\d{1,2}[.)]|[①-⑳]|[❍○□■◆◇▶▷])\s*"
)
_SECTION_LABEL_PREFIX = re.compile(
    r"^(?:[❍○□■◆◇▶▷※＊*]|[①-⑳]|\(?\d{1,2}[.)]|"
    r"[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+[.)]?|[가-힣][.)])\s*"
)
_MARKDOWN_HEADING = re.compile(r"^#{1,6}\s+\S")
_CHAPTER_HEADING = re.compile(
    r"^(?:Chapter\b|CHAPTER\b|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\s+|제\s*\d+\s*장\b)"
)
_SEPARATOR = re.compile(r"^(?:={3,}|-{3,}|\*{3,})$")
_SECTION_HEADINGS = frozenset(
    {
        "지원대상",
        "신청자격",
        "신청대상",
        "신청방법",
        "신청기간",
        "신청절차",
        "지원내용",
        "지원금액",
        "지원기준",
        "지급방법",
        "지급시기",
        "지급기간",
        "제출서류",
        "구비서류",
        "유의사항",
        "선정방법",
        "선정기준",
        "문의처",
        "사업기간",
        "지원제외",
        "지원제외대상",
        "제외대상",
        "중복지원",
        "중복수혜",
        "소득요건",
        "재산요건",
        "접수기간",
        "신청안내",
        "지원안내",
        "기타",
        "비고",
    }
)


def _source_lines(text: str) -> list[str]:
    return text.splitlines(keepends=True)


def _line_body(line: str) -> str:
    return line.rstrip("\r\n")


def _header_name(line: str) -> str | None:
    match = _FIELD_HEADER.fullmatch(_line_body(line))
    return match.group(1) if match is not None else None


def api_field_ranges(text: str) -> tuple[ApiFieldRange, ...]:
    """Return header-delimited fields with trailing blank source lines omitted."""
    lines = _source_lines(text)
    headers = [
        (number, name)
        for number, line in enumerate(lines, 1)
        if (name := _header_name(line)) is not None
    ]
    result = []
    for index, (start, name) in enumerate(headers):
        raw_end = headers[index + 1][0] - 1 if index + 1 < len(headers) else len(lines)
        end = start
        for number in range(raw_end, start - 1, -1):
            if _line_body(lines[number - 1]).strip():
                end = number
                break
        result.append(
            ApiFieldRange(
                name=name,
                start_line=start,
                end_line=end,
                kind=_FIELD_KINDS.get(name, "unknown"),
            )
        )
    return tuple(result)


def _valid_range(line_count: int, start: int, end: int) -> bool:
    return type(start) is int and type(end) is int and 1 <= start <= end <= line_count


def _containing_field(
    fields: tuple[ApiFieldRange, ...], start: int, end: int
) -> ApiFieldRange | None:
    return next(
        (field for field in fields if field.start_line <= start <= end <= field.end_line),
        None,
    )


def field_kind(text: str, start: int, end: int) -> str | None:
    """Classify one selection only when it stays inside one known API field."""
    if not _valid_range(len(_source_lines(text)), start, end):
        return None
    field = _containing_field(api_field_ranges(text), start, end)
    if field is None or field.kind == "unknown":
        return None
    return field.kind


def _is_section_heading(body: str) -> bool:
    stripped = body.strip()
    if (
        _MARKDOWN_HEADING.match(stripped)
        or _CHAPTER_HEADING.match(stripped)
        or _SEPARATOR.fullmatch(stripped)
    ):
        return True
    candidate = _SECTION_LABEL_PREFIX.sub("", stripped)
    candidate = candidate.split(":", 1)[0].split("：", 1)[0]
    return "".join(candidate.split()) in _SECTION_HEADINGS


def _is_transaction_formula(line: str) -> bool:
    body = _line_body(line)
    relation = _FORMULA_RELATION.search(body)
    if relation is None:
        return False
    left = _SECTION_LABEL_PREFIX.sub("", body[: relation.start()].strip()).strip()
    if _TRANSACTION_AMOUNT_LABEL.fullmatch(left) is None:
        return False
    right = body[relation.end() :].strip()
    if not right:
        return True
    return bool(_ARITHMETIC.search(right) or _FORMULA_TERM.search(right))


def _is_formula_continuation(body: str) -> bool:
    if not body.strip() or _is_section_heading(body):
        return False
    stripped = body.strip()
    if stripped.startswith(("※", "＊", "*")):
        return True
    if body[:1].isspace():
        return True
    if _LIST_PREFIX.match(stripped):
        return bool(
            _ARITHMETIC.search(stripped)
            or _FORMULA_TERM.search(stripped)
            or _CONDITION_CUE.search(stripped)
        )
    return bool(_FORMULA_REFERENCE.search(stripped) or _CONDITION_CUE.search(stripped))


def _eligibility_formula_context(
    lines: list[str],
    start: int,
    end: int,
) -> tuple[int, int]:
    next_line = end + 1
    if next_line > len(lines) or _header_name(lines[next_line - 1]) is not None:
        return (start, end)
    if not _is_transaction_formula(lines[next_line - 1]):
        return (start, end)
    last = next_line
    for number in range(next_line + 1, len(lines) + 1):
        line = lines[number - 1]
        if _header_name(line) is not None or not _is_formula_continuation(line):
            break
        last = number
    return (start, last)


def evidence_context(
    text: str,
    kind: str,
    start: int,
    end: int,
) -> tuple[int, int]:
    """Restore structural source context, or retain the caller's range unchanged."""
    lines = _source_lines(text)
    if (
        type(kind) is not str
        or kind not in _EVIDENCE_KINDS
        or not _valid_range(len(lines), start, end)
    ):
        return (start, end)
    fields = api_field_ranges(text)
    field = _containing_field(fields, start, end)
    if field is not None:
        if field.kind == "unknown" and kind != "source_context":
            return (start, end)
        return (field.start_line, field.end_line)
    if kind != "eligibility" or any(
        item.start_line <= end and item.end_line >= start for item in fields
    ):
        return (start, end)
    return _eligibility_formula_context(lines, start, end)
