"""Extract exact won literals from F12 policy evidence text."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
import re


@dataclass(frozen=True)
class WonMention:
    """A source literal and its half-open span in the original text."""

    start: int
    end: int
    amounts_won: tuple[int, ...]


_NUMBER = r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?"
_HSPACE = r"[ \t\u00a0]*"
_UNIT = r"(?:억|만|천)"
_LARGE_UNIT = r"(?:억|만|천|백|십|조)"
_RANGE_MARK = r"[~∼〜～]"
_BAD_LEFT = r"[\d.,A-Za-z_%억만천백십조원+−－*/×÷=^~∼〜～]"
_BAD_RIGHT = r"(?![\d억만천백십조원])(?!\.\d)(?!,\d)"

_RANGE_RE = re.compile(
    rf"(?<!{_BAD_LEFT})"
    rf"(?P<left_number>{_NUMBER})"
    rf"(?:{_HSPACE}(?P<left_unit>{_UNIT}))?"
    rf"(?P<left_won>{_HSPACE}원)?"
    rf"{_HSPACE}{_RANGE_MARK}{_HSPACE}"
    rf"(?P<right_number>{_NUMBER})"
    rf"(?:{_HSPACE}(?P<right_unit>{_UNIT}))?"
    rf"{_HSPACE}원"
    rf"{_BAD_RIGHT}"
)

_SINGLE_RE = re.compile(
    rf"(?<!{_BAD_LEFT})"
    rf"(?P<number>{_NUMBER})"
    rf"(?:{_HSPACE}(?P<unit>{_UNIT}))?"
    rf"{_HSPACE}원"
    rf"{_BAD_RIGHT}"
)

# A single amount can be the suffix of an unsupported compound such as
# "1억 2만원" or "1만 5000원". Block the whole compound before scanning singles.
_COMPOSITE_RE = re.compile(
    rf"(?<!{_BAD_LEFT})"
    rf"{_NUMBER}{_HSPACE}{_LARGE_UNIT}"
    rf"(?:{_HSPACE}{_NUMBER}(?:{_HSPACE}{_LARGE_UNIT})?)+"
    rf"{_HSPACE}원"
    rf"{_BAD_RIGHT}"
)

_UNSUPPORTED_PREFIX_RE = re.compile(
    rf"{_NUMBER}{_HSPACE}{_LARGE_UNIT}{_HSPACE}$"
)
_NEGATIVE_PREFIX_RE = re.compile(
    rf"[-−－]{_HSPACE}(?:약|대략)?{_HSPACE}$"
)

_UNIT_FACTORS = {
    "천": 1_000,
    "만": 10_000,
    "억": 100_000_000,
}
_EXPRESSION_MARKS = frozenset("+-−－*/×÷=^xX~∼〜～")
_HORIZONTAL_SPACE = " \t\u00a0"


def won_mentions(text: str) -> tuple[WonMention, ...]:
    """Return supported won literals in source order.

    start and end are a half-open span, so text[start:end] is the complete
    literal from its first digit through its final won symbol. Amounts in a
    range remain in source order. Unsupported compounds and arithmetic are
    left uninterpreted rather than reduced to a partial amount.
    """

    if not isinstance(text, str):
        raise TypeError("text must be str")

    blocked: list[tuple[int, int]] = []
    found: list[WonMention] = []

    for match in _COMPOSITE_RE.finditer(text):
        blocked.append(match.span())

    for match in _RANGE_RE.finditer(text):
        blocked.append(match.span())
        if _has_unsupported_prefix(text, match.start()):
            continue
        amounts = _range_amounts(match)
        if amounts is None or _touches_expression(text, match.start(), match.end()):
            continue
        found.append(WonMention(match.start(), match.end(), amounts))

    for match in _SINGLE_RE.finditer(text):
        if _overlaps_blocked(match.start(), match.end(), blocked):
            continue
        if _has_unsupported_prefix(text, match.start()):
            continue
        amount = _to_won(match.group("number"), match.group("unit"))
        if amount is None or _touches_expression(text, match.start(), match.end()):
            continue
        found.append(WonMention(match.start(), match.end(), (amount,)))

    found.sort(key=lambda mention: mention.start)
    return tuple(found)


def _range_amounts(match: re.Match[str]) -> tuple[int, int] | None:
    left_unit = match.group("left_unit")
    right_unit = match.group("right_unit")
    left_has_won = match.group("left_won") is not None

    if not left_has_won:
        if left_unit is None:
            left_unit = right_unit
        elif right_unit != left_unit:
            # A unit attached only to the left side is not an explicit shared
            # unit range. Leave the whole expression unresolved.
            return None

    left = _to_won(match.group("left_number"), left_unit)
    right = _to_won(match.group("right_number"), right_unit)
    if left is None or right is None or left > right:
        return None
    return left, right


def _to_won(number: str, unit: str | None) -> int | None:
    factor = _UNIT_FACTORS.get(unit, 1)
    literal = Decimal(number.replace(",", ""))
    with localcontext() as context:
        context.prec = len(literal.as_tuple().digits) + 2
        amount = literal * factor
    integral = amount.to_integral_value()
    if integral < 0 or amount != integral:
        return None
    return int(integral)


def _has_unsupported_prefix(text: str, start: int) -> bool:
    prefix = text[:start]
    return bool(
        _UNSUPPORTED_PREFIX_RE.search(prefix)
        or _NEGATIVE_PREFIX_RE.search(prefix)
    )


def _touches_expression(text: str, start: int, end: int) -> bool:
    before = text[:start].rstrip(_HORIZONTAL_SPACE)
    after = text[end:].lstrip(_HORIZONTAL_SPACE)
    return (
        (bool(before) and before[-1] in _EXPRESSION_MARKS)
        or (bool(after) and after[0] in _EXPRESSION_MARKS)
    )


def _overlaps_blocked(
    start: int,
    end: int,
    blocked: list[tuple[int, int]],
) -> bool:
    return any(
        blocked_start < end and start < blocked_end
        for blocked_start, blocked_end in blocked
    )
