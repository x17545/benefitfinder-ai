"""Bounded HTTP access to explicitly allowed public official documents."""

from __future__ import annotations

import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

from benefitfinder.application.collection import SourceResponse
from benefitfinder.application.official_documents import (
    MAX_DOCUMENT_BYTES,
    OfficialDocumentTarget,
    public_document_url,
)

MAX_RESPONSE_BYTES = MAX_DOCUMENT_BYTES
MAX_ALLOWED_URLS = 100
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MIN_INTERVAL_SECONDS = 0.5
DEFAULT_MAX_RETRIES = 2
DEFAULT_RETRY_BACKOFF_SECONDS = 0.5
MAX_RETRY_BACKOFF_SECONDS = 2.0
_USER_AGENT = "benefitfinder-ai/0.1 (official public-document collection)"
_CREDENTIAL_PARTS = ("key", "token", "secret", "password")


class OfficialDocumentHttpError(RuntimeError):
    """A fixed adapter failure that never includes a URL or transport text."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        # urllib turns a 3xx response into HTTPError instead of following it.
        return None


def _safe_public_url(value: object) -> bool:
    if type(value) is not str or not public_document_url(value):
        return False
    decoded = urllib.parse.unquote(value)
    return not any(ord(character) < 32 or ord(character) == 127 for character in decoded)


class OfficialDocumentHttpFetcher:
    """Fetch one allowlisted public document without following redirects."""

    def __init__(
        self,
        allowed_urls: tuple[str, ...],
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        min_interval_seconds: float = DEFAULT_MIN_INTERVAL_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        opener: object | None = None,
    ) -> None:
        if (
            type(allowed_urls) is not tuple
            or not 1 <= len(allowed_urls) <= MAX_ALLOWED_URLS
            or any(not _safe_public_url(url) for url in allowed_urls)
            or type(timeout_seconds) not in (int, float)
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= DEFAULT_TIMEOUT_SECONDS
            or type(min_interval_seconds) not in (int, float)
            or not math.isfinite(min_interval_seconds)
            or min_interval_seconds < 0
            or type(max_retries) is not int
            or not 0 <= max_retries <= DEFAULT_MAX_RETRIES
            or type(retry_backoff_seconds) not in (int, float)
            or not math.isfinite(retry_backoff_seconds)
            or not 0 <= retry_backoff_seconds <= MAX_RETRY_BACKOFF_SECONDS
            or type(max_response_bytes) is not int
            or not 1 <= max_response_bytes <= MAX_RESPONSE_BYTES
            or not callable(sleep)
            or not callable(monotonic)
            or (opener is not None and not callable(getattr(opener, "open", None)))
        ):
            raise ValueError("invalid_official_document_http_config")
        self._allowed_urls = allowed_urls
        self._allowed_url_set = frozenset(allowed_urls)
        self._timeout = float(timeout_seconds)
        self._min_interval = float(min_interval_seconds)
        self._max_retries = max_retries
        self._retry_backoff = float(retry_backoff_seconds)
        self._max_response_bytes = max_response_bytes
        self._sleep = sleep
        self._monotonic = monotonic
        self._opener = urllib.request.build_opener(_NoRedirect()) if opener is None else opener
        self._last_request_at: float | None = None

    def __repr__(self) -> str:
        return f"{type(self).__name__}(allowed_urls={len(self._allowed_urls)})"

    def _wait_turn(self) -> None:
        if self._last_request_at is None:
            return
        remaining = self._min_interval - (self._monotonic() - self._last_request_at)
        if remaining > 0:
            self._sleep(remaining)

    def _read_bounded(self, stream: object) -> bytes:
        reader = getattr(stream, "read", None)
        if not callable(reader):
            raise OfficialDocumentHttpError("invalid_response_body")
        body = reader(self._max_response_bytes + 1)
        if type(body) is not bytes:
            raise OfficialDocumentHttpError("invalid_response_body")
        if len(body) > self._max_response_bytes:
            raise OfficialDocumentHttpError("response_too_large")
        return body

    @staticmethod
    def _content_type(headers: object) -> str | None:
        getter = getattr(headers, "get", None)
        if not callable(getter):
            return None
        value = getter("Content-Type")
        return value if type(value) is str else None

    def _send(self, http_request: urllib.request.Request) -> SourceResponse:
        self._wait_turn()
        try:
            with self._opener.open(http_request, timeout=self._timeout) as response:
                status = response.getcode()
                if type(status) is not int:
                    raise OfficialDocumentHttpError("invalid_http_status")
                return SourceResponse(
                    status_code=status,
                    body=self._read_bounded(response),
                    content_type=self._content_type(response.headers),
                )
        except urllib.error.HTTPError as error:
            try:
                if type(error.code) is not int:
                    raise OfficialDocumentHttpError("invalid_http_status")
                body = self._read_bounded(error) if error.fp is not None else b""
                return SourceResponse(
                    status_code=error.code,
                    body=body,
                    content_type=self._content_type(error.headers),
                )
            finally:
                error.close()
        finally:
            self._last_request_at = self._monotonic()

    def _validate_target(self, target: OfficialDocumentTarget) -> None:
        if not isinstance(target, OfficialDocumentTarget):
            raise OfficialDocumentHttpError("invalid_target")
        try:
            url = target.url
            method = target.method
            form = target.form
            if type(url) is not str or not _safe_public_url(url):
                raise OfficialDocumentHttpError("invalid_target")
            if url not in self._allowed_url_set:
                raise OfficialDocumentHttpError("url_not_allowed")
            if type(method) is not str or method not in ("GET", "POST"):
                raise OfficialDocumentHttpError("invalid_target")
            if type(form) is not tuple or len(form) > 8 or (method == "GET" and form):
                raise OfficialDocumentHttpError("invalid_target")
            names: set[str] = set()
            for pair in form:
                if type(pair) is not tuple or len(pair) != 2:
                    raise OfficialDocumentHttpError("invalid_target")
                name, value = pair
                if (
                    type(name) is not str
                    or type(value) is not str
                    or re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name) is None
                    or name in names
                    or not 1 <= len(value) <= 500
                    or not value.isprintable()
                    or any(part in name.lower() for part in _CREDENTIAL_PARTS)
                ):
                    raise OfficialDocumentHttpError("invalid_target")
                names.add(name)
        except OfficialDocumentHttpError:
            raise
        except (AttributeError, TypeError, ValueError):
            raise OfficialDocumentHttpError("invalid_target") from None

    def fetch(self, target: OfficialDocumentTarget) -> SourceResponse:
        """Fetch one validated target, retrying only transport, 429, and 5xx failures."""
        self._validate_target(target)
        headers = {"User-Agent": _USER_AGENT, "Accept": "*/*"}
        data: bytes | None = None
        if target.method == "POST":
            encoded = urllib.parse.urlencode(dict(target.form), doseq=False)
            data = encoded.encode("ascii")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        try:
            http_request = urllib.request.Request(
                target.url,
                data=data,
                headers=headers,
                method=target.method,
            )
        except (TypeError, ValueError, UnicodeError):
            raise OfficialDocumentHttpError("invalid_target") from None

        for attempt in range(self._max_retries + 1):
            if attempt:
                backoff = min(
                    self._retry_backoff * 2 ** (attempt - 1),
                    MAX_RETRY_BACKOFF_SECONDS,
                )
                self._sleep(backoff)
            try:
                response = self._send(http_request)
            except OfficialDocumentHttpError:
                raise
            except Exception:
                if attempt == self._max_retries:
                    raise OfficialDocumentHttpError("transport_failed") from None
                continue
            if response.status_code == 429 or 500 <= response.status_code <= 599:
                if attempt == self._max_retries:
                    return response
                continue
            return response
        raise AssertionError("unreachable retry loop")
