"""Source-only clause retrieval and context closure; never interpret eligibility or payments."""

from __future__ import annotations

import re

_BOUNDARY = re.compile(r"^\s*(?:[❍○□■]|[0-9]{1,2}\.\s|Q[.:：]|[ⅠⅡⅢⅣⅤⅥⅦⅧ]+\s|《|<|\[표\s*\d)")
_ELIGIBILITY_SECTION = re.compile(
    r"^[^가-힣]{0,12}(?:지원\s*대상|신청\s*자격|(?:신청\s*)?지원\s*제외)"
    r"(?:\s*대상)?(?=\s*(?:[:：>》]|$))"
)
_OTHER_SECTION = re.compile(
    r"^[^가-힣]{0,12}(?:신청\s*(?:방법|기간|절차|서류)|지원\s*(?:내용|금액|방법)|"
    r"지급\s*(?:방법|시기)|(?:제출|구비)\s*서류|(?:기타\s*)?유의사항|선정\s*(?:방법|절차))"
)
INCOME = re.compile(r"소득|중위|건강보험료")
_EXCEPTION = re.compile(r"※|예외|다만|단\s*[,，:]|불인정|선발.*제외")
_ADJUSTMENT = re.compile(r"차감|차액|공제|감액|뺀|빼고|잔여")
_BENEFIT = re.compile(r"지원|지급|수급|수혜|급여")
_INCOME_ONLY = re.compile(r"소득|재산|건강보험료|기준\s*중위")
_PAYMENT = re.compile(r"지원금|지원액|지급액|차액")
_AMOUNT_NOTE = re.compile(r"실제|생애|기존|기수혜|수혜자|지급받|지원받|회차|횟수|차감|차액|공제|감액|잔여")
_HEADER_ONLY = re.compile(
    r"^\s*(?:[0-9]{1,2}\.|[ⅠⅡⅢⅣⅤⅥⅦⅧ]+)?\s*[❍○□■*<《]*\s*"
    r"(?:신청\s*)?(?:지원\s*)?(?:대상|자격|제외\s*대상|제외)\s*[>》:：]?\s*$"
)


def clause_blocks(lines: list[str]) -> tuple[dict[int, str], ...]:
    """Keep subordinate bullets, wrapped lines and exception notes with their parent clause."""
    blocks: list[dict[int, str]] = []
    current: dict[int, str] = {}
    for number, text in enumerate(lines, 1):
        if current and _BOUNDARY.search(text):
            blocks.append(current)
            current = {}
        current[number] = text
    if current:
        blocks.append(current)
    return tuple(blocks)


def eligibility_blocks(blocks: tuple[dict[int, str], ...]) -> tuple[dict[int, str], ...]:
    result = []
    active = False
    for block in blocks:
        first = next(iter(block.values()))
        if _ELIGIBILITY_SECTION.search(first):
            active = True
        elif _OTHER_SECTION.search(first):
            active = False
        if active:
            result.append({n: text for n, text in block.items() if not _HEADER_ONLY.fullmatch(text)})
    return tuple(result)


def income_exceptions(blocks: tuple[dict[int, str], ...]) -> tuple[dict[int, str], ...]:
    return tuple(block for block in blocks
                 if INCOME.search(text := "\n".join(block.values())) and _EXCEPTION.search(text))


def amount_adjustments(blocks: tuple[dict[int, str], ...]) -> tuple[dict[int, str], ...]:
    """Find separately located benefit deductions, including Q&A; exclude income-only limits."""
    return tuple(
        block for block in blocks
        if _ADJUSTMENT.search(text := "\n".join(block.values()))
        and _BENEFIT.search(text)
        and (not _INCOME_ONLY.search(text) or _PAYMENT.search(text))
    )


def is_amount_note(line: str) -> bool:
    return bool(_AMOUNT_NOTE.search(line))


def is_amount_adjustment(text: str) -> bool:
    return bool(_ADJUSTMENT.search(text) and _BENEFIT.search(text))


def shown_context(blocks: tuple[dict[int, str], ...]) -> dict[int, str]:
    shown = {number: text for block in blocks for number, text in block.items()}
    # Refuse oversized/incomplete context rather than silently dropping qualifying lines.
    if len(shown) > 240 or sum(map(len, shown.values())) > 35_000 or any(
        len(text) > 700 for text in shown.values()
    ):
        raise ValueError("policy_facts_context_limit")
    return shown
