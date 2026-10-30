"""온통청년 API HTTP 어댑터 (application.collection.SourceFetcher 구현).

인증키는 이 어댑터만 알고, 요청 기록·스냅숏·오류에는 남기지 않는다. 표준 라이브러리만
사용하며 요청 사이에 최소 간격을 둬 공식 서버에 부담을 주지 않는다. 같은 요청이 5xx나
연결 오류로 실패하면 간격을 늘려 몇 번만 다시 시도한다. 실행 단위 재시도·스케줄은 jobs가 맡는다.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

from benefitfinder.application.collection import SourceRequest, SourceResponse

DEFAULT_USER_AGENT = "benefitfinder-ai/0.1 (youth policy data collection)"


class YouthcenterHttpFetcher:
    """요청 파라미터에 인증키를 붙여 호출하고, HTTP 오류 응답도 그대로 돌려준다."""

    def __init__(
        self,
        api_key: str,
        *,
        key_param: str = "apiKeyNm",
        timeout_seconds: float = 30.0,
        min_interval_seconds: float = 0.5,
        user_agent: str = DEFAULT_USER_AGENT,
        require_https: bool = True,
        max_retries: int = 3,
        retry_backoff_seconds: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not api_key.strip():
            raise ValueError("인증키가 비어 있습니다.")
        self._api_key = api_key.strip()
        self._key_param = key_param
        self._timeout = timeout_seconds
        self._min_interval = min_interval_seconds
        self._user_agent = user_agent
        self._require_https = require_https
        if max_retries < 0 or retry_backoff_seconds < 0:
            raise ValueError("재시도 횟수와 대기 시간은 0 이상이어야 합니다.")
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff_seconds
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
        for attempt in range(self._max_retries + 1):
            if attempt:
                self._sleep(self._retry_backoff * 2 ** (attempt - 1))
            try:
                response = self._send(http_request)
            except (urllib.error.URLError, TimeoutError):
                if attempt == self._max_retries:
                    raise
                continue
            if response.status_code < 500 or attempt == self._max_retries:
                return response
        raise AssertionError("도달하지 않는 경로")

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
