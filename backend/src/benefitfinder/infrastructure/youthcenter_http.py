"""온통청년 API HTTP 어댑터 (application.collection.SourceFetcher 구현).

인증키는 이 어댑터만 알고, 요청 기록·스냅숏·오류에는 남기지 않는다. 표준 라이브러리만
사용하며 요청 사이에 최소 간격(기본 2초)을 둬 공식 서버에 부담을 주지 않는다. 같은 요청이 5xx나
연결 오류로 실패하면 간격을 늘려 몇 번만 다시 시도한다. 첫 페이지 이후 페이지가 400·403으로
거절되면(온통청년이 연속 호출을 일시적으로 막는 경우) 길게 쉰 뒤 같은 페이지를 최대 2번 다시 요청한다.
첫 페이지 거절은 인증키 자체 문제일 수 있으므로 기다리지 않고 그대로 돌려준다.
실행 단위 재시도·스케줄은 jobs가 맡는다.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

from benefitfinder.application.collection import (
    SourceRequest,
    SourceResponse,
    UnsafeSourceResponse,
)

DEFAULT_USER_AGENT = "benefitfinder-ai/0.1 (youth policy data collection)"


class YouthcenterHttpFetcher:
    """인증키를 붙여 호출하고, 키를 포함하지 않은 응답만 원본 그대로 돌려준다."""

    def __init__(
        self,
        api_key: str,
        *,
        key_param: str = "apiKeyNm",
        timeout_seconds: float = 30.0,
        min_interval_seconds: float = 2.0,
        user_agent: str = DEFAULT_USER_AGENT,
        require_https: bool = True,
        max_retries: int = 3,
        retry_backoff_seconds: float = 2.0,
        page_param: str = "pageNum",
        throttle_statuses: frozenset[int] = frozenset({400, 403}),
        throttle_retries: int = 2,
        throttle_wait_seconds: float = 45.0,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not api_key.strip():
            raise ValueError("인증키가 비어 있습니다.")
        self._api_key = api_key.strip()
        encoded_keys = {
            self._api_key,
            urllib.parse.quote(self._api_key, safe=""),
            urllib.parse.quote_plus(self._api_key, safe=""),
            json.dumps(self._api_key, ensure_ascii=True)[1:-1],
            json.dumps(self._api_key, ensure_ascii=False)[1:-1],
        }
        # Providers may echo a request URL or a JSON-escaped credential in errors.
        encoded_keys |= {value.replace("/", r"\/") for value in tuple(encoded_keys)}
        self._credential_patterns = tuple(value.encode("utf-8") for value in encoded_keys)
        self._key_param = key_param
        self._timeout = timeout_seconds
        self._min_interval = min_interval_seconds
        self._user_agent = user_agent
        self._require_https = require_https
        if (
            max_retries < 0
            or retry_backoff_seconds < 0
            or throttle_retries < 0
            or throttle_wait_seconds < 0
        ):
            raise ValueError("재시도 횟수와 대기 시간은 0 이상이어야 합니다.")
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff_seconds
        self._page_param = page_param
        self._throttle_statuses = frozenset(throttle_statuses)
        self._throttle_retries = throttle_retries
        self._throttle_wait = throttle_wait_seconds
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request_at: float | None = None

    def __repr__(self) -> str:
        return f"{type(self).__name__}(key_param={self._key_param!r}, key=***)"

    def _wait_turn(self) -> None:
        if self._last_request_at is None:
            return
        remaining = self._min_interval - (self._monotonic() - self._last_request_at)
        if remaining > 0:
            self._sleep(remaining)

    def fetch(self, request: SourceRequest) -> SourceResponse:
        if self._key_param in request.params:
            raise ValueError("요청 파라미터에 인증키가 이미 들어 있습니다.")
        if self._require_https and not request.url.startswith("https://"):
            raise ValueError("https 주소만 호출합니다.")
        params = {**request.params, self._key_param: self._api_key}
        url = f"{request.url}?{urllib.parse.urlencode(params)}"
        http_request = urllib.request.Request(
            url, headers={"User-Agent": self._user_agent, "Accept": "application/json"}
        )
        attempt = 0  # 5xx·연결 오류 재시도 횟수
        throttled = 0  # 첫 페이지 이후 400·403 재시도 횟수
        while True:
            try:
                response = self._send(http_request)
            except (urllib.error.URLError, TimeoutError):
                if attempt == self._max_retries:
                    raise
                attempt += 1
                self._sleep(self._retry_backoff * 2 ** (attempt - 1))
                continue
            for raw in (response.body, (response.content_type or "").encode("utf-8")):
                normalized = re.sub(rb"%[0-9a-fA-F]{2}", lambda match: match[0].upper(), raw)
                if any(pattern in raw or pattern in normalized
                       for pattern in self._credential_patterns):
                    raise UnsafeSourceResponse()
            if (
                response.status_code in self._throttle_statuses
                and self._is_later_page(request)
                and throttled < self._throttle_retries
            ):
                throttled += 1
                self._sleep(self._throttle_wait)
                continue
            if response.status_code < 500 or attempt == self._max_retries:
                return response
            attempt += 1
            self._sleep(self._retry_backoff * 2 ** (attempt - 1))

    def _is_later_page(self, request: SourceRequest) -> bool:
        """첫 페이지는 앞선 호출이 없으므로 거절을 호출 제한으로 보지 않는다."""
        page = request.params.get(self._page_param)
        return isinstance(page, str) and page.isdigit() and int(page) > 1

    def _send(self, http_request: urllib.request.Request) -> SourceResponse:
        self._wait_turn()
        try:
            with urllib.request.urlopen(http_request, timeout=self._timeout) as response:
                return SourceResponse(
                    status_code=response.status,
                    body=response.read(),
                    content_type=response.headers.get("Content-Type"),
                )
        except urllib.error.HTTPError as error:
            body = error.read() if error.fp is not None else b""
            return SourceResponse(
                status_code=error.code,
                body=body,
                content_type=error.headers.get("Content-Type") if error.headers else None,
            )
        finally:
            self._last_request_at = self._monotonic()
