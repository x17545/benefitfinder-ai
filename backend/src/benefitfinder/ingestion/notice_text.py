"""공식 공고 HTML·PDF 페이지를 Agent 입력용 텍스트로 바꾼다 (notice-text-v2).

결과 텍스트는 ``SourceDocument.text``가 되고, 그 SHA-256이 검수 기록과 연결된다. 그래서 같은
입력은 항상 같은 텍스트가 되도록 규칙을 고정한다. 규칙을 바꾸면 ``NOTICE_TEXT_FORMAT``을
올리고, 이전 형식으로 만든 텍스트와 섞지 않는다.

- HTML: 스크립트·스타일·메뉴·머리말·꼬리말·입력 요소를 빼고 본문만 남긴다. ``main``·``article``·
  ``role="main"``·``id="content(s)"`` 영역이 있으면 그 안만 쓴다. 블록 요소는 빈 줄로 나눈
  문단, ``br``·목록 항목·표의 행은 한 줄, 표의 칸은 `` | ``로 잇는다.
- PDF: 페이지 텍스트를 받아 페이지마다 ``=== PDF PAGE N ===`` 표식을 붙인다. PDF 파일에서
  페이지 텍스트를 꺼내는 일은 이 모듈 밖의 어댑터가 맡는다.
- 공통: 줄 안의 연속 공백은 한 칸, 빈 줄은 하나로 줄이고 유니코드 NFC로 맞춘다. 700자를
  넘는 줄은 공백에서 나눈다. 내용은 요약하거나 고치지 않는다.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from html.parser import HTMLParser

from benefitfinder.application.source_chunks import plan_source_chunks

NOTICE_TEXT_FORMAT = "notice-text-v2"
MAX_LINE_CHARS = 700
PDF_PAGE_MARKER = "=== PDF PAGE {number} ==="

NOTICE_DECODE_FAILED = "notice_decode_failed"
NOTICE_TEXT_EMPTY = "notice_text_empty"

_SKIP_TAGS = frozenset(
    {
        "script", "style", "noscript", "template", "svg", "iframe", "head", "nav",
        "header", "footer", "aside", "textarea", "button", "select", "option", "object",
    }
)
_VOID_TAGS = frozenset(
    {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
        "param", "source", "track", "wbr",
    }
)
_BLOCK_TAGS = frozenset(
    {
        "address", "article", "blockquote", "caption", "dd", "details", "dialog", "div",
        "dl", "dt", "fieldset", "figcaption", "figure", "h1", "h2", "h3", "h4", "h5",
        "h6", "hr", "main", "ol", "p", "pre", "section", "summary", "table", "tbody",
        "tfoot", "thead", "ul",
    }
)
_LINE_TAGS = frozenset({"br", "li", "tr"})
_CELL_TAGS = frozenset({"td", "th"})
_MAIN_IDS = frozenset({"content", "contents"})
# 수집 중에는 구조를 표식으로만 남기고, 마지막에 한 번에 줄·문단·칸으로 바꾼다.
_PARA, _LINE, _CELL = "\x1d", "\x1e", "\x1f"
_SPACES = re.compile(r"[ \t\r\f\v  -​  　﻿]+")
_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_.:-]+)""", re.I)
_HEADER_CHARSET = re.compile(r"charset\s*=\s*\"?([A-Za-z0-9_.:-]+)", re.I)


class NoticeTextError(ValueError):
    """고정 코드로만 실패를 알린다. 원문 내용은 메시지에 넣지 않는다."""


def decode_html(body: bytes, content_type: str | None) -> str:
    """응답 헤더 → HTML meta → UTF-8 → CP949 순서로 엄격하게 디코딩한다."""
    candidates: list[str] = []
    if content_type and (match := _HEADER_CHARSET.search(content_type)):
        candidates.append(match.group(1))
    if match := _META_CHARSET.search(body[:4096]):
        candidates.append(match.group(1).decode("ascii", errors="ignore"))
    candidates += ["utf-8", "cp949"]
    for name in candidates:
        encoding = "cp949" if name.lower() in {"euc-kr", "ks_c_5601-1987", "ksc5601"} else name
        try:
            return body.decode(encoding).removeprefix("﻿")
        except (LookupError, UnicodeDecodeError):
            continue
    raise NoticeTextError(NOTICE_DECODE_FAILED)


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool]] = []  # (tag, starts main region)
        self.skip_depth = 0
        self.main_depth = 0
        self.pre_depth = 0
        self.all_parts: list[str] = []
        self.main_parts: list[str] = []

    def _emit(self, value: str) -> None:
        if self.skip_depth:
            return
        self.all_parts.append(value)
        if self.main_depth:
            self.main_parts.append(value)

    def _break(self, tag: str, *, opening: bool = True) -> None:
        if tag in _BLOCK_TAGS:
            self._emit(_PARA)
        elif tag in _LINE_TAGS:
            self._emit(_LINE)
        elif tag in _CELL_TAGS and opening:
            self._emit(_CELL)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.lower(): (value or "") for name, value in attrs}
        if tag in _VOID_TAGS:
            if tag in {"br", "hr"}:
                self._break(tag)
            return
        starts_main = (
            tag in {"main", "article"}
            or values.get("role", "").lower() == "main"
            or values.get("id", "").lower() in _MAIN_IDS
        )
        self.stack.append((tag, starts_main))
        if tag in _SKIP_TAGS:
            self.skip_depth += 1
            return
        if starts_main:
            self.main_depth += 1
        if tag == "pre":
            self.pre_depth += 1
        self._break(tag)
        if tag == "li":
            self._emit("- ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"br", "hr"}:
            self._break(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID_TAGS or all(open_tag != tag for open_tag, _ in self.stack):
            return
        while self.stack:
            open_tag, starts_main = self.stack.pop()
            if open_tag in _SKIP_TAGS:
                self.skip_depth -= 1
            else:
                self._break(open_tag, opening=False)
                if starts_main:
                    self.main_depth -= 1
                if open_tag == "pre":
                    self.pre_depth -= 1
            if open_tag == tag:
                break

    def handle_data(self, data: str) -> None:
        if self.pre_depth:
            self._emit(data)
        else:
            self._emit(_SPACES.sub(" ", data.replace("\n", " ")))


def _wrap(line: str) -> list[str]:
    pieces: list[str] = []
    while len(line) > MAX_LINE_CHARS:
        cut = line.rfind(" ", 0, MAX_LINE_CHARS + 1)
        if cut <= 0:
            cut = MAX_LINE_CHARS
        pieces.append(line[:cut].rstrip())
        line = line[cut:].lstrip()
    pieces.append(line)
    return pieces


def _apply_markers(text: str) -> str:
    """표식 묶음을 줄바꿈으로 바꾼다. 문단 표식이 하나라도 있으면 빈 줄 하나로 나눈다."""
    text = re.sub(
        f"[ \\t]*(?:[{_PARA}{_LINE}][ \\t]*)+",
        lambda match: "\n\n" if _PARA in match.group() else "\n",
        text,
    )
    text = re.sub(f"(^|\\n)[ \\t]*{_CELL}[ \\t]*", r"\1", text)
    return re.sub(f"[ \\t]*{_CELL}[ \\t]*", " | ", text)


def _tidy(text: str) -> str:
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    lines: list[str] = []
    for raw in text.split("\n"):
        line = _SPACES.sub(" ", raw).strip()
        if not line:
            if lines and lines[-1]:
                lines.append("")
            continue
        lines.extend(_wrap(line))
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def html_to_text(html: str) -> str:
    """HTML 문서를 notice-text-v2 텍스트로 바꾼다. 본문이 없으면 ``notice_text_empty``."""
    collector = _Collector()
    collector.feed(html)
    collector.close()
    main_text = _tidy(_apply_markers("".join(collector.main_parts)))
    text = main_text or _tidy(_apply_markers("".join(collector.all_parts)))
    if not text:
        raise NoticeTextError(NOTICE_TEXT_EMPTY)
    return text + "\n"


def select_html_region(html: str, content_id: str) -> str:
    """Return one exact original element, including images/attributes, or fail closed."""
    offsets = [0]
    for match in re.finditer('\n',html):
        offsets.append(match.end())

    class Region(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=False)
            self.stack: list[tuple[str,int | None]] = []
            self.regions: list[tuple[int,int]] = []

        def position(self) -> int:
            line,column = self.getpos()
            return offsets[line-1]+column

        def handle_starttag(self, tag: str, attrs: list[tuple[str,str | None]]) -> None:
            if tag not in _VOID_TAGS:
                start = self.position() if dict(attrs).get('id') == content_id else None
                self.stack.append((tag,start))

        def handle_endtag(self, tag: str) -> None:
            if any(open_tag == tag for open_tag,_ in self.stack):
                while self.stack:
                    open_tag,start = self.stack.pop()
                    if start is not None:
                        end = html.find('>',self.position())
                        if end < 0 or open_tag != tag:
                            raise NoticeTextError('notice_region_invalid')
                        self.regions.append((start,end+1))
                    if open_tag == tag:
                        break

    parser = Region()
    parser.feed(html)
    parser.close()
    if len(parser.regions) != 1 or any(start is not None for _,start in parser.stack):
        raise NoticeTextError('notice_region_invalid')
    start,end = parser.regions[0]
    return html[start:end]


def pdf_pages_to_text(pages: Sequence[str]) -> str:
    """PDF 페이지 텍스트에 1부터 매긴 페이지 표식을 붙인다. 빈 페이지도 번호를 유지한다."""
    if not pages:
        raise NoticeTextError(NOTICE_TEXT_EMPTY)
    blocks: list[str] = []
    for number, page in enumerate(pages, 1):
        body = _tidy(page)
        marker = PDF_PAGE_MARKER.format(number=number)
        blocks.append(f"{marker}\n{body}" if body else marker)
    if not any(_tidy(page) for page in pages):
        raise NoticeTextError(NOTICE_TEXT_EMPTY)
    return "\n\n".join(blocks) + "\n"


def fits_chunked_extraction(text: str) -> bool:
    """서윤 님 쪽 분할 추출(문단·페이지와 앞뒤 문맥 220줄·6,000자)에 들어가는지 확인한다."""
    try:
        plan_source_chunks(text)
    except ValueError:
        return False
    return True
