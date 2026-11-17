"""Bounded, content-free diagnostics for youth policy source failures."""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser

from benefitfinder.application.collection import SourceResponse
from benefitfinder.ingestion.versioning import sha256_hex
from benefitfinder.ingestion.youthcenter import YouthcenterApiConfig

CONTENT_TYPES = frozenset({
    "application/json", "text/html", "application/xml", "text/xml",
    "text/plain", "application/octet-stream", "other",
})
RESPONSE_FORMATS = frozenset({"json", "html", "xml", "text", "empty", "binary"})
IDENTIFIER_SOURCES = frozenset({"errorCode", "resultCode", "html"})
_PARSE_LIMIT = 65536


def bounded_code(value: object) -> str | None:
    """Allow only the provider's short code syntax, never free-form messages."""
    if type(value) is int and 100 <= value <= 999:
        return str(value)
    if isinstance(value, str) and re.fullmatch(r"(?:e[0-9]{3}|[0-9]{3})", value):
        return value
    return None


class _HtmlIdentifier(HTMLParser):
    """Read only an exact code in a title or a labelled error-code element."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._tag: str | None = None
        self._parts: list[str] = []
        self._ignored_tag: str | None = None
        self.codes: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._ignored_tag = tag
        if self._ignored_tag is not None:
            return
        if self._tag is None and (
            tag == "title" or dict(attrs).get("id") == "error-code"
        ):
            self._tag = tag
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._tag is not None and self._ignored_tag is None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._ignored_tag is not None:
            if tag == self._ignored_tag:
                self._ignored_tag = None
            return
        if tag == self._tag:
            code = bounded_code("".join(self._parts).strip())
            if code is not None:
                self.codes.add(code)
            self._tag = None
            self._parts = []


def response_diagnostic(
    config: YouthcenterApiConfig, response: SourceResponse,
) -> dict[str, str | int | None]:
    """Describe the response without logging source text, URLs or header parameters."""
    media_type = (response.content_type or "").split(";", 1)[0].strip().lower()
    content_type = (
        media_type if media_type in CONTENT_TYPES else "other"
    ) if media_type else None
    body = response.body
    identifier = None
    source = None
    api_code = None
    # Large bodies are fingerprinted, but only bounded bodies are parsed for diagnostics.
    try:
        document = json.loads(body) if len(body) <= _PARSE_LIMIT else None
        is_json = len(body) <= _PARSE_LIMIT
    except (ValueError, TypeError, RecursionError):
        document, is_json = None, False
    try:
        text = body[:_PARSE_LIMIT].decode("utf-8-sig")
    except UnicodeError:
        text = None
    if not body:
        response_format = "empty"
    elif is_json:
        response_format = "json"
        if isinstance(document, dict):
            api_code = bounded_code(document.get("errorCode"))
            if api_code is not None:
                source = "errorCode"
            elif config.result_code_path is not None:
                value = document
                for key in config.result_code_path:
                    value = value.get(key) if isinstance(value, dict) else None
                api_code = bounded_code(value)
                if api_code is not None:
                    source = "resultCode"
        identifier = api_code
    elif text is not None and (media_type == "text/html" or re.search(
        r"<(?:!doctype\s+html|html|head|body|title)(?:\s|>)", text, re.IGNORECASE,
    )):
        response_format = "html"
        parser = _HtmlIdentifier()
        try:
            parser.feed(text)
        except (ValueError, AssertionError):
            # Malformed declarations must not hide the original HTTP failure.
            parser.codes.clear()
        if len(parser.codes) == 1:
            identifier = next(iter(parser.codes))
            source = "html"
    elif text is not None and text.lstrip().startswith("<"):
        response_format = "xml"
    elif text is None or any(ord(char) < 32 and char not in "\r\n\t" for char in text):
        response_format = "binary"
    else:
        response_format = "text"
    return {
        "api_code": api_code,
        "response_format": response_format,
        "content_type": content_type,
        "response_bytes": len(body),
        "response_sha256": sha256_hex(body),
        "error_identifier": identifier,
        "error_identifier_source": source,
    }
