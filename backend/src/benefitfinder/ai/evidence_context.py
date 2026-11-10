"""Structural source context only: no policy interpretation or approval reference input."""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class EvidenceGroup:
    kind: str
    start: int
    end: int


_QUESTION = re.compile(r"^\s*Q\s*[0-9]+(?:\s|[.．:：])")
_ANSWER = re.compile(r"^\s*A(?:\s|[.．:：])")
_NOTE = re.compile(r"^\s*[*＊]+\s*\S")
_PRIMARY = re.compile(r"^\s*[-•●▶❍○□■※]\s*\S")
_PAGE = re.compile(r"^\s*=== PDF PAGE ([1-9][0-9]*) ===\s*$")
_HEADINGS = {
    '지원내용', '지원대상', '지원요건', '지원기준', '지원제외대상', '제외대상',
    '신청방법', '신청절차', '신청및접수', '소득·재산조사', '지원결정및통보',
    '청년월세지원금지급', '이의신청', '사후관리', '지원한도', '지급방법', '지급시기', '중복방지', '기준준용',
}


def _heading(line: str) -> bool:
    return ''.join(line.split()) in _HEADINGS or bool(re.match(
        r'^\s*(?:Chapter\b|CHAPTER\b|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\s)|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\s*$', line
    ))


def context_groups(text: str) -> tuple[EvidenceGroup, ...]:
    """Find explicit Q/A units and parent paragraphs carrying star footnotes.

    Page labels are layout, not semantic boundaries. Blank paragraphs, primary
    bullets and explicit section headings keep neighboring subjects separate.
    Unanswered questions (e.g. a contents page) do not create a FAQ group.
    """
    lines = text.splitlines()
    if not lines:
        return ()
    pages = [i for i, line in enumerate(lines) if _PAGE.match(line)]
    layout = set(pages)
    for i in pages:
        if i + 1 < len(lines) and lines[i + 1].strip() == _PAGE.match(lines[i])[1]:
            layout.add(i + 1)
    layout.update(i for i, line in enumerate(lines) if line.strip() in {'Q&A', 'Chapter'})
    questions = [i for i, line in enumerate(lines) if _QUESTION.match(line)]
    groups = []
    for index, start in enumerate(questions):
        end = questions[index + 1] if index + 1 < len(questions) else len(lines)
        # A new explicit section or a non-FAQ page ends the preceding FAQ.
        for i in range(start + 1, end):
            if _heading(lines[i]):
                end = i
                break
            if i in pages:
                next_page = next((p for p in pages if p > i), len(lines))
                if not any(_QUESTION.match(x) or _ANSWER.match(x) or x.strip() == 'Q&A'
                           for x in lines[i:next_page]):
                    end = i
                    break
        answer = next((i for i in range(start + 1, end) if _ANSWER.match(lines[i])), None)
        if answer is None:
            continue
        while end > answer + 1 and (not lines[end - 1].strip() or end - 1 in layout):
            end -= 1
        groups.append(EvidenceGroup('faq', start + 1, end))

    boundaries = {0, len(lines)}
    for i, line in enumerate(lines):
        if _heading(line):
            boundaries.add(i)
        if i in layout:
            continue
        if _QUESTION.match(line) or _ANSWER.match(line) or _PRIMARY.match(line) or _heading(line):
            boundaries.add(i)
        if not line.strip() and not any(abs(i - p) <= 2 for p in pages):
            boundaries.update((i, i + 1))
    starts = sorted(boundaries)
    for start, end in zip(starts, starts[1:]):
        notes = [i for i in range(start, end) if _NOTE.match(lines[i])]
        if not notes:
            continue
        while start < end and (not lines[start].strip() or start in layout):
            start += 1
        while end > start and (not lines[end - 1].strip() or end - 1 in layout):
            end -= 1
        if start < notes[0] and any(lines[i].strip() and i not in layout
                                    for i in range(start, notes[0])):
            groups.append(EvidenceGroup('footnote', start + 1, end))
    return tuple(sorted(set(groups), key=lambda g: (g.start, g.end, g.kind)))


def close_context(
    ranges: tuple[tuple[int, int], ...], groups: tuple[EvidenceGroup, ...]
) -> tuple[tuple[int, int], ...]:
    """Restore complete overlapping units; never fill gaps with unselected words."""
    if any(not 1 <= a <= b for a, b in ranges) or any(
        g.kind not in {'faq', 'footnote'} or not 1 <= g.start <= g.end for g in groups
    ):
        raise ValueError('chunk_context_invalid')
    current = tuple(sorted(set(ranges)))
    while True:
        extended = set(current)
        for group in groups:
            if any(a <= group.end and b >= group.start for a, b in current):
                extended.add((group.start, group.end))
        merged = []
        for a, b in sorted(extended):
            if merged and a <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        result = tuple(merged)
        if result == current:
            return result
        current = result
