"""Conservative overlap-restriction spans for source-fact publication.

Flags Korean duplicate-benefit restriction and ambiguous mentions without
assuming permission. Absence of a span never means allowed; callers hold
when any span is unresolved. Only a proven simple explicit conflict with a
uniquely resolved target may be kept, never a qualified or negated mention.
Pure helper: no DB, network, or model calls.
"""

import re

__all__ = ["overlap_spans"]

# Explicit restriction: overlap cue with optional action and restriction cue.
# Whitespace (including line breaks) is allowed between syllables and tokens,
# with a short clause window so distant sentences are not merged.
_EXPLICIT = re.compile(
    r"(?:중\s*복|동\s*시|이\s*중|병\s*급)"
    r"\s*(?:수\s*급|수\s*혜|지\s*원|참\s*여|선\s*정|지\s*급|수\s*령|신\s*청|혜\s*택)?"
    r"\s*[^.!?]{0,30}?\s*"
    r"(?:불\s*가|금\s*지|제\s*한|제\s*외|배\s*제|불\s*가\s*능|불\s*인\s*정|불\s*허|할\s*수\s*없|되\s*지\s*않)"
)

# Business-duplicate mention even without an explicit ban word, e.g. similar
# business duplicate participation. Conservative: treat as needing review.
_BUSINESS_DUP = re.compile(
    r"(?:동\s*일|유\s*사|동\s*종)"
    r"\s*[\u00b7\u318d,\uff0c\s]*\s*(?:동\s*일|유\s*사|동\s*종)?\s*[\u00b7\u318d,\uff0c\s]*\s*"
    r"(?:사\s*업|제\s*도|정\s*책|지\s*원\s*금|급\s*여|수\s*당)"
    r"\s*[^.!?]{0,20}?\s*(?:중\s*복|동\s*시)"
    r"\s*(?:수\s*급|수\s*혜|지\s*원|참\s*여|선\s*정|지\s*급|수\s*령|신\s*청|혜\s*택)?"
)

# Other-business restriction without needing an explicit duplicate word, e.g.
# other business participation is limited. Requires a restriction cue so a
# plain confirmation request alone is not flagged here.
_OTHER_RESTRICT = re.compile(
    r"(?:타|다\s*른)\s*[^.!?]{0,10}?\s*(?:사\s*업|제\s*도|정\s*책|수\s*당|급\s*여|지\s*원\s*금)"
    r"\s*[^.!?]{0,30}?\s*(?:수\s*급|수\s*혜|지\s*원|참\s*여|선\s*정|지\s*급|수\s*령|신\s*청|혜\s*택)?"
    r"\s*[^.!?]{0,30}?\s*(?:불\s*가|금\s*지|제\s*한|제\s*외|배\s*제|불\s*가\s*능|불\s*인\s*정|불\s*허)"
)

# Ambiguous confirmation need with an overlap cue but no explicit ban, e.g.
# duplicate receipt needs confirmation. Conservative: needs review.
_AMBIG_CONFIRM = re.compile(
    r"(?:중\s*복|동\s*시|이\s*중)"
    r"\s*(?:수\s*급|수\s*혜|지\s*원|참\s*여|선\s*정|지\s*급|수\s*령|신\s*청|혜\s*택)?"
    r"\s*[^.!?]{0,20}?\s*(?:여\s*부|확\s*인|문\s*의|검\s*토)"
)

_EMPLOYMENT_PROGRAM = r"국민\s*취업\s*지원\s*제도"
_NAMED_EXCLUSION = re.compile(
    _EMPLOYMENT_PROGRAM + r"[^.!?\n]{0,24}?(?:참여자|수급자|수혜자)[^.!?\n]{0,16}?(?:제외|불가|제한)"
    r"|제외\s*대상[^.!?\n]{0,24}?" + _EMPLOYMENT_PROGRAM + r"\s*(?:참여자|수급자|수혜자)"
)

_PATTERNS = (_EXPLICIT, _BUSINESS_DUP, _OTHER_RESTRICT, _AMBIG_CONFIRM, _NAMED_EXCLUSION)


def overlap_spans(text: str) -> tuple[tuple[int, int], ...]:
    """Return char spans flagging possible overlap restrictions."""
    if not isinstance(text, str) or not text:
        return ()
    found: list[tuple[int, int]] = []
    for pattern in _PATTERNS:
        for match in pattern.finditer(text):
            start, end = match.span()
            if 0 <= start < end <= len(text):
                found.append((start, end))
    if not found:
        return ()
    found.sort()
    merged: list[list[int]] = [[found[0][0], found[0][1]]]
    for start, end in found[1:]:
        last = merged[-1]
        if start <= last[1]:
            if end > last[1]:
                last[1] = end
        else:
            merged.append([start, end])
    return tuple((s, e) for s, e in merged)
