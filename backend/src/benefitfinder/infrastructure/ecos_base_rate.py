"""Official Bank of Korea ECOS base-rate HTTP adapter.

The adapter uses the fixed ECOS host, validates the complete daily series, and returns
only the latest observation by its ECOS date. Credentials, request URLs, and response
bodies are never attached to errors or object representations.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from http.client import HTTPSConnection
from zoneinfo import ZoneInfo

from benefitfinder.application.base_rates import (
    BaseRateObservation,
    BaseRateUnavailable,
)

HOST = "ecos.bok.or.kr"
REQUEST_TIMEOUT_SECONDS = 10
MAX_RESPONSE_BYTES = 1_048_576

_KST = ZoneInfo("Asia/Seoul")
_ERROR = "ecos_base_rate_unavailable"
_STAT_CODE = "722Y001"
_ITEM_CODE = "0101000"
_UNIT_NAME = "연%"
_WINDOW_DAYS = 30
_PAGE_START = 1
_PAGE_SIZE = 1000
_KEY_PATTERN = re.compile(r"[A-Za-z0-9]+")
_TIME_PATTERN = re.compile(r"[0-9]{8}")


def kst_today() -> date:
    """Return the current date in Korea Standard Time."""

    return datetime.now(_KST).date()


def utc_now() -> datetime:
    """Return the current timezone-aware UTC time."""

    return datetime.now(timezone.utc)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise ValueError


def _fail() -> BaseRateUnavailable:
    return BaseRateUnavailable(_ERROR)


def _parse_row(row: object, *, start: date, today: date) -> tuple[date, Decimal]:
    if not isinstance(row, dict):
        raise _fail()
    if row.get("STAT_CODE") != _STAT_CODE or row.get("ITEM_CODE1") != _ITEM_CODE:
        raise _fail()
    if any(
        key not in row or row[key] is not None for key in ("ITEM_CODE2", "ITEM_CODE3", "ITEM_CODE4")
    ):
        raise _fail()
    if "CYCLE" in row and row["CYCLE"] != "D":
        raise _fail()
    if row.get("UNIT_NAME") != _UNIT_NAME:
        raise _fail()

    raw_time = row.get("TIME")
    if not isinstance(raw_time, str) or _TIME_PATTERN.fullmatch(raw_time) is None:
        raise _fail()
    try:
        observed_on = datetime.strptime(raw_time, "%Y%m%d").date()
    except ValueError:
        raise _fail() from None
    if not start <= observed_on <= today:
        raise _fail()

    raw_value = row.get("DATA_VALUE")
    if not isinstance(raw_value, str):
        raise _fail()
    try:
        percent = Decimal(raw_value.strip())
    except (InvalidOperation, ValueError):
        raise _fail() from None
    if not percent.is_finite() or not 0 <= percent <= 100:
        raise _fail()
    annual_rate = percent / Decimal(100)
    if not math.isfinite(float(annual_rate)):
        raise _fail()
    return observed_on, annual_rate


def _parse_response(
    raw: bytes,
    *,
    start: date,
    today: date,
    checked_at: datetime,
) -> BaseRateObservation:
    document = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_unique_object,
        parse_constant=_reject_json_constant,
    )
    if not isinstance(document, dict) or "RESULT" in document:
        raise _fail()
    search = document.get("StatisticSearch")
    if not isinstance(search, dict):
        raise _fail()

    total_count = search.get("list_total_count")
    rows = search.get("row")
    if type(total_count) is not int or total_count < 0 or not isinstance(rows, list) or not rows:
        raise _fail()
    if total_count != len(rows):
        raise _fail()

    by_date: dict[date, Decimal] = {}
    for row in rows:
        observed_on, annual_rate = _parse_row(row, start=start, today=today)
        previous = by_date.get(observed_on)
        if previous is not None and previous != annual_rate:
            raise _fail()
        by_date[observed_on] = annual_rate
    if not by_date:
        raise _fail()

    latest_on = max(by_date)
    return BaseRateObservation(
        annual_rate=float(by_date[latest_on]),
        observed_on=latest_on,
        checked_at=checked_at,
    )


class EcosBaseRate:
    """Fetch the official daily base-rate series from ECOS."""

    def __init__(
        self,
        api_key: str,
        *,
        today: Callable[[], date] = kst_today,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        if not isinstance(api_key, str) or _KEY_PATTERN.fullmatch(api_key) is None:
            raise _fail()
        self._api_key = api_key
        self._today = today
        self._now = now

    def __repr__(self) -> str:
        return f"{type(self).__name__}(api_key=***)"

    def fetch(self) -> BaseRateObservation:
        try:
            today = self._today()
            checked_at = self._now()
            if not isinstance(today, date) or isinstance(today, datetime):
                raise _fail()
            if (
                not isinstance(checked_at, datetime)
                or checked_at.tzinfo is None
                or checked_at.utcoffset() is None
            ):
                raise _fail()
            start = today - timedelta(days=_WINDOW_DAYS)
            path = (
                f"/api/StatisticSearch/{self._api_key}/json/kr/{_PAGE_START}/{_PAGE_SIZE}/"
                f"{_STAT_CODE}/D/{start:%Y%m%d}/{today:%Y%m%d}/{_ITEM_CODE}"
            )
            connection = HTTPSConnection(HOST, timeout=REQUEST_TIMEOUT_SECONDS)
            try:
                connection.request(
                    "GET",
                    path,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "benefitfinder-ai/0.1",
                    },
                )
                response = connection.getresponse()
                if response.status != 200:
                    raise _fail()
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if not isinstance(raw, bytes) or len(raw) > MAX_RESPONSE_BYTES:
                    raise _fail()
            finally:
                connection.close()
            return _parse_response(raw, start=start, today=today, checked_at=checked_at)
        except BaseRateUnavailable:
            raise
        except Exception:
            raise _fail() from None
