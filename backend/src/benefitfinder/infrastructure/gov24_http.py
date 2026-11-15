"""Bounded GOV24 HTTP adapter with fixed endpoints and masked credentials."""

from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

from benefitfinder.application.collection import SourceRequest, SourceResponse

GOV24_ENDPOINTS = frozenset(
    {
        "https://api.odcloud.kr/api/gov24/v3/serviceList",
        "https://api.odcloud.kr/api/gov24/v3/serviceDetail",
        "https://api.odcloud.kr/api/gov24/v3/supportConditions",
    }
)
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
_QUERY_ID = "cond[서비스ID::EQ]"
_QUERY_KEYS = frozenset({_QUERY_ID, "page", "perPage", "returnType"})
_USER_AGENT = "benefitfinder-ai/0.1 (gov24 public-service collection)"


class Gov24HttpError(RuntimeError):
    """A fixed adapter failure that never includes transport text or credentials."""

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
        # Returning None makes urllib surface the 3xx as HTTPError without following it.
        return None


class Gov24HttpFetcher:
    """Fetch the three fixed GOV24 endpoints through the documented auth header."""

    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = 30.0,
        min_interval_seconds: float = 0.5,
        max_retries: int = 3,
        retry_backoff_seconds: float = 2.0,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        opener: object | None = None,
    ) -> None:
        key = api_key.strip() if isinstance(api_key, str) else ""
        if not key or any(ord(character) < 32 or ord(character) == 127 for character in key):
            raise ValueError("GOV24 API key is invalid")
        if timeout_seconds <= 0 or min_interval_seconds < 0:
            raise ValueError("GOV24 timeout and pacing settings are invalid")
        if max_retries < 0 or retry_backoff_seconds < 0:
            raise ValueError("GOV24 retry settings are invalid")
        if not 1 <= max_response_bytes <= MAX_RESPONSE_BYTES:
            raise ValueError("GOV24 response limit is invalid")
        self._api_key = key
        self._timeout = timeout_seconds
        self._min_interval = min_interval_seconds
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff_seconds
        self._max_response_bytes = max_response_bytes
        self._sleep = sleep
        self._monotonic = monotonic
        self._opener = (
            urllib.request.build_opener(_NoRedirect()) if opener is None else opener
        )
        self._last_request_at: float | None = None

    def __repr__(self) -> str:
        return f"{type(self).__name__}(endpoints=3, authorization=***)"

    def _validate_request(self, request: SourceRequest) -> None:
        if request.url not in GOV24_ENDPOINTS:
            raise ValueError("GOV24 endpoint is not allowed")
        params = request.params
        if not isinstance(params, dict) or set(params) != _QUERY_KEYS:
            raise ValueError("GOV24 query shape is not allowed")
        if any(type(value) is not str for value in params.values()):
            raise ValueError("GOV24 query values must be strings")
        service_id = params[_QUERY_ID]
        if not service_id or len(service_id) > 200:
            raise ValueError("GOV24 service ID is invalid")
        if params["page"] != "1" or params["perPage"] != "1":
            raise ValueError("GOV24 requests are one record per page")
        if params["returnType"] != "JSON":
            raise ValueError("GOV24 requests require JSON")

    def _wait_turn(self) -> None:
        if self._last_request_at is None:
            return
        remaining = self._min_interval - (self._monotonic() - self._last_request_at)
        if remaining > 0:
            self._sleep(remaining)

    def _read_bounded(self, stream: object) -> bytes:
        reader = getattr(stream, "read")
        body = reader(self._max_response_bytes + 1)
        if type(body) is not bytes:
            raise Gov24HttpError("invalid_response_body")
        if len(body) > self._max_response_bytes:
            raise Gov24HttpError("response_too_large")
        return body

    def _send(self, http_request: urllib.request.Request) -> SourceResponse:
        self._wait_turn()
        try:
            with self._opener.open(http_request, timeout=self._timeout) as response:
                status = response.getcode()
                if type(status) is not int:
                    raise Gov24HttpError("invalid_http_status")
                return SourceResponse(
                    status_code=status,
                    body=self._read_bounded(response),
                    content_type=response.headers.get("Content-Type"),
                )
        except urllib.error.HTTPError as error:
            try:
                body = self._read_bounded(error) if error.fp is not None else b""
            finally:
                error.close()
            return SourceResponse(
                status_code=error.code,
                body=body,
                content_type=error.headers.get("Content-Type") if error.headers else None,
            )
        finally:
            self._last_request_at = self._monotonic()

    def fetch(self, request: SourceRequest) -> SourceResponse:
        """Fetch one validated request, retrying only 429, 5xx, and transport failures."""
        self._validate_request(request)
        params = dict(request.params)
        url = f"{request.url}?{urllib.parse.urlencode(params)}"
        http_request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Infuser {self._api_key}",
                "Accept": "application/json",
                "User-Agent": _USER_AGENT,
            },
        )
        for attempt in range(self._max_retries + 1):
            if attempt:
                self._sleep(self._retry_backoff * 2 ** (attempt - 1))
            try:
                response = self._send(http_request)
            except Gov24HttpError:
                raise
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempt == self._max_retries:
                    raise Gov24HttpError("transport_failed") from None
                continue
            if not (response.status_code == 429 or 500 <= response.status_code <= 599):
                return response
            if attempt == self._max_retries:
                return response
        raise AssertionError("unreachable retry loop")
