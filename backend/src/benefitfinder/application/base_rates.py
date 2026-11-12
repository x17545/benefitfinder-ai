"""Application contracts for the Bank of Korea base rate."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol
from zoneinfo import ZoneInfo

_KST = ZoneInfo("Asia/Seoul")
_ANNUAL_RATE_INVALID = "base_rate_annual_rate_invalid"
_CHECKED_AT_INVALID = "base_rate_checked_at_invalid"
_OBSERVED_ON_INVALID = "base_rate_observed_on_invalid"
_OBSERVED_ON_FUTURE = "base_rate_observed_on_future"


@dataclass(frozen=True)
class BaseRateObservation:
    """One verified base-rate observation expressed as an annual ratio."""

    annual_rate: float
    observed_on: date
    checked_at: datetime

    def __post_init__(self) -> None:
        if (
            isinstance(self.annual_rate, bool)
            or not isinstance(self.annual_rate, (int, float))
            or not 0 <= self.annual_rate <= 1
            or not math.isfinite(self.annual_rate)
        ):
            raise ValueError(_ANNUAL_RATE_INVALID)
        if (
            not isinstance(self.checked_at, datetime)
            or self.checked_at.tzinfo is None
            or self.checked_at.utcoffset() is None
        ):
            raise ValueError(_CHECKED_AT_INVALID)
        if not isinstance(self.observed_on, date) or isinstance(self.observed_on, datetime):
            raise ValueError(_OBSERVED_ON_INVALID)
        if self.observed_on > self.checked_at.astimezone(_KST).date():
            raise ValueError(_OBSERVED_ON_FUTURE)


class BaseRateUnavailable(RuntimeError):
    """The verified base rate could not be obtained or validated."""


class BaseRateRepository(Protocol):
    """Persistence boundary for the latest verified base rate."""

    def latest(self) -> BaseRateObservation | None:
        """Return the latest stored observation.

        ``None`` means the repository is empty. Raise ``BaseRateUnavailable`` when
        retrieval itself fails.
        """
        ...

    def save(self, observation: BaseRateObservation) -> None:
        """Persist one validated observation."""
        ...


class BaseRateSource(Protocol):
    """Official-source boundary for fetching a base-rate observation."""

    def fetch(self) -> BaseRateObservation:
        """Fetch and validate the latest available observation."""
        ...
