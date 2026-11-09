"""Bounded, overlapping source windows; never change source text or line numbers."""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class SourceChunk:
    index: int
    core_start: int
    core_end: int
    start: int
    end: int


def plan_source_chunks(
    text: str, *, max_lines: int = 220, max_chars: int = 6000
) -> tuple[SourceChunk, ...]:
    """Cover every line once in cores, with a complete neighboring unit on both sides.

    Units are extracted PDF pages, or whole blank-separated paragraphs for plain text.
    Oversized units/context fail before any model request instead of being truncated.
    The limits include context, not just the non-overlapping core.
    """
    if not text.strip() or type(max_lines) is not int or type(max_chars) is not int:
        raise ValueError("chunk_input_invalid")
    if not 1 <= max_lines <= 220 or not 1 <= max_chars <= 6000:
        raise ValueError("chunk_input_invalid")
    lines = text.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if re.fullmatch(
        r"=== PDF PAGE [1-9][0-9]* ===", line.strip()
    )]
    if not starts:
        starts = [i for i in range(1, len(lines)) if not lines[i - 1].strip()
                  and lines[i].strip()]
    starts = sorted({0, *starts})
    units = list(zip(starts, starts[1:] + [len(lines)], strict=True))
    result = []
    core = 0
    while core < len(units):
        chosen = None
        # Prefer two core pages/paragraphs, shrink only the core, never the halo.
        for stop in range(min(core + 2, len(units)), core, -1):
            left, right = max(0, core - 1), min(len(units), stop + 1)
            start, end = units[left][0], units[right - 1][1]
            if end - start <= max_lines and sum(len(x) for x in lines[start:end]) <= max_chars:
                chosen = SourceChunk(len(result) + 1, units[core][0] + 1,
                                     units[stop - 1][1], start + 1, end)
                break
        if chosen is None:
            raise ValueError("chunk_context_limit")
        result.append(chosen)
        core = stop
    return tuple(result)


def evidence_schema(visible_lines: tuple[int, ...]) -> dict[str, object]:
    """Constrain selections to actual nonblank lines; blank lines are not evidence."""
    if (not visible_lines or len(visible_lines) > 220
            or any(type(n) is not int or n < 1 for n in visible_lines)
            or tuple(sorted(set(visible_lines))) != visible_lines):
        raise ValueError("chunk_input_invalid")
    return {
        "type": "object",
        "required": ["eligibility", "amount", "timing", "exceptions"],
        "additionalProperties": False,
        "properties": {kind: {
            "type": "array", "maxItems": 220,
            "items": {"type": "integer", "enum": list(visible_lines)},
        } for kind in ("eligibility", "amount", "timing", "exceptions")},
    }
