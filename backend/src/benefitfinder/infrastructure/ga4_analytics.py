"""Fixed-destination GA4 Measurement Protocol adapter for privacy-safe events."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
import json
import math
import re
import ssl
from typing import Any, cast
from urllib.parse import parse_qsl, urlencode, urlsplit
import uuid

from benefitfinder.application.analytics import AnalyticsEventRecord, AnalyticsParameter

GA4_COLLECT_URL = "https://www.google-analytics.com/mp/collect"

_GA4_HOST = "www.google-analytics.com"
_GA4_PATH = "/mp/collect"
_MEASUREMENT_ID_RE = re.compile(r"^G-[A-Z0-9]{10}$")
_CLIENT_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_POLICY_ID_RE = re.compile(r"^[A-Za-z0-9:_-]{1,100}$")
_MAX_SECRET_LENGTH = 256
_MAX_STATUS_LINE_BYTES = 1024

AnalyticsTransport = Callable[[str, bytes, Mapping[str, str]], Awaitable[int]]
_ParameterValidator = Callable[[object], bool]


def _is_int_between(minimum: int, maximum: int) -> _ParameterValidator:
    def validate(value: object) -> bool:
        return type(value) is int and minimum <= value <= maximum

    return validate


def _is_one_of(*allowed: str) -> _ParameterValidator:
    values = frozenset(allowed)

    def validate(value: object) -> bool:
        return type(value) is str and value in values

    return validate


def _is_bool(value: object) -> bool:
    return type(value) is bool


def _is_policy_id(value: object) -> bool:
    return type(value) is str and _POLICY_ID_RE.fullmatch(value) is not None


_PARAMETER_RULES: dict[str, tuple[dict[str, _ParameterValidator], frozenset[str]]] = {
    "consent_check": ({}, frozenset()),
    "remember_on": ({}, frozenset()),
    "step_view": ({"step": _is_int_between(1, 13)}, frozenset({"step"})),
    "step_complete": ({"step": _is_int_between(1, 13)}, frozenset({"step"})),
    "step_skip": ({}, frozenset()),
    "analyze_start": ({}, frozenset()),
    "analyze_success": (
        {
            "duration_ms": _is_int_between(0, 120_000),
            "portfolio_count": _is_int_between(0, 3),
        },
        frozenset({"duration_ms", "portfolio_count"}),
    ),
    "analyze_fail": (
        {
            "code": _is_one_of(
                "http_error",
                "invalid_request",
                "unsupported_domain",
                "not_found",
                "method_not_allowed",
                "daily_limit_exceeded",
                "dependency_unavailable",
                "internal_error",
                "calculation_limit_exceeded",
                "timeout",
                "network_error",
                "invalid_response",
                "unknown",
            )
        },
        frozenset({"code"}),
    ),
    "period_change": (
        {"period": _is_one_of("1y", "2y", "all")},
        frozenset({"period"}),
    ),
    "pv_toggle": ({"on": _is_bool}, frozenset({"on"})),
    "combo_select": ({"rank": _is_int_between(1, 3)}, frozenset({"rank"})),
    "detail_view": ({"policy_id": _is_policy_id}, frozenset({"policy_id"})),
    "apply_click": (
        {
            "policy_id": _is_policy_id,
            "from": _is_one_of("result", "detail"),
            "rank": _is_int_between(1, 3),
        },
        frozenset({"policy_id", "from"}),
    ),
    "whatif_open": ({}, frozenset()),
    "whatif_select": (
        {"scenario": _is_one_of("employed", "income_up", "independent", "age_plus_one")},
        frozenset({"scenario"}),
    ),
    "image_save": ({}, frozenset()),
    "clear_mine": ({}, frozenset()),
}


def ga_client_id(client_id: object) -> str | None:
    """Convert the API UUIDv4 to GA's decimal upper/lower 64-bit client ID."""
    if type(client_id) is not str or _CLIENT_ID_RE.fullmatch(client_id) is None:
        return None
    value = uuid.UUID(client_id)
    upper = value.int >> 64
    lower = value.int & ((1 << 64) - 1)
    return f"{upper}.{lower}"


def _sanitize_parameters(
    name: object,
    parameters: object,
) -> dict[str, AnalyticsParameter] | None:
    if type(name) is not str:
        return None
    rule = _PARAMETER_RULES.get(name)
    if rule is None or not isinstance(parameters, Mapping):
        return None
    validators, required = rule
    result: dict[str, AnalyticsParameter] = {}
    for key, value in parameters.items():
        if type(key) is not str or key not in validators:
            return None
        if value is None:
            # The API contract keeps rank:null as unknown; GA rejects null values.
            if name == "apply_click" and key == "rank":
                continue
            return None
        if not validators[key](value):
            return None
        result[key] = cast(AnalyticsParameter, value)
    if not required.issubset(result):
        return None
    return result


def _valid_api_secret(value: object) -> bool:
    return (
        type(value) is str
        and 0 < len(value) <= _MAX_SECRET_LENGTH
        and not any(character.isspace() or ord(character) < 32 for character in value)
    )


async def _post_google_measurement_protocol(
    url: str,
    body: bytes,
    headers: Mapping[str, str],
) -> int:
    """POST directly to the fixed endpoint without redirects or caller headers."""
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != _GA4_HOST
        or parsed.path != _GA4_PATH
        or parsed.fragment
        or {key for key, _ in parse_qsl(parsed.query, keep_blank_values=True)}
        != {"measurement_id", "api_secret"}
    ):
        raise ValueError("invalid_ga4_endpoint")
    if headers != {"Content-Type": "application/json"}:
        raise ValueError("invalid_ga4_headers")

    target = parsed.path + "?" + parsed.query
    request = (
        f"POST {target} HTTP/1.1\r\n"
        f"Host: {_GA4_HOST}\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n"
        "\r\n"
    ).encode("ascii") + body
    reader, writer = await asyncio.open_connection(
        _GA4_HOST,
        443,
        ssl=ssl.create_default_context(),
        server_hostname=_GA4_HOST,
        limit=_MAX_STATUS_LINE_BYTES,
    )
    try:
        writer.write(request)
        await writer.drain()
        status_line = await reader.readline()
        parts = status_line.split(maxsplit=2)
        if len(parts) < 2 or parts[0] != b"HTTP/1.1" or not parts[1].isdigit():
            raise ValueError("invalid_ga4_response")
        status = int(parts[1])
        writer.close()
        await writer.wait_closed()
        return status
    except BaseException:
        # Cancellation must not enter another unbounded TLS shutdown await.
        writer.transport.abort()
        raise


class GA4MeasurementProtocolAnalytics:
    """Bounded, timeout-limited sender with no retry, queue, or persistence."""

    def __init__(
        self,
        measurement_id: str,
        api_secret: str,
        *,
        timeout_seconds: float = 1.0,
        max_in_flight: int = 4,
        transport: AnalyticsTransport | None = None,
    ) -> None:
        if (
            type(measurement_id) is not str
            or _MEASUREMENT_ID_RE.fullmatch(measurement_id) is None
            or not _valid_api_secret(api_secret)
        ):
            raise ValueError("invalid_analytics_configuration")
        if (
            not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 5
            or type(max_in_flight) is not int
            or not 1 <= max_in_flight <= 16
        ):
            raise ValueError("invalid_analytics_configuration")
        self._timeout_seconds = timeout_seconds
        self._max_in_flight = max_in_flight
        self._in_flight = 0
        self._transport = transport or _post_google_measurement_protocol
        self._url = GA4_COLLECT_URL + "?" + urlencode(
            {"measurement_id": measurement_id, "api_secret": api_secret}
        )

    @staticmethod
    def _payload(event: AnalyticsEventRecord) -> dict[str, Any] | None:
        client_id = ga_client_id(event.client_id)
        if client_id is None:
            return None
        if type(event.session_id) is not int or not 1 <= event.session_id <= 9_999_999_999:
            return None
        parameters = _sanitize_parameters(event.name, event.parameters)
        if parameters is None:
            return None
        parameters["session_id"] = event.session_id
        parameters["engagement_time_msec"] = 1
        return {
            "client_id": client_id,
            "events": [{"name": event.name, "params": parameters}],
            "consent": {
                "ad_user_data": "DENIED",
                "ad_personalization": "DENIED",
            },
        }

    async def send(self, event: AnalyticsEventRecord) -> None:
        """Drop rather than queue when bounded capacity is unavailable."""
        payload = self._payload(event)
        if payload is None or self._in_flight >= self._max_in_flight:
            return
        self._in_flight += 1
        try:
            body = json.dumps(
                payload,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
            async with asyncio.timeout(self._timeout_seconds):
                await self._transport(self._url, body, {"Content-Type": "application/json"})
        except Exception:
            return
        finally:
            self._in_flight -= 1
