"""Shared daily analysis admission; stores no request or profile information."""

from typing import Protocol

DAILY_ANALYSIS_LIMIT = 100


class DailyAnalysisLimitExceeded(Exception):
    """The current Korean calendar day has already admitted 100 analyses."""


class AnalysisUsageUnavailable(Exception):
    """Admission cannot be checked; fail closed instead of bypassing the limit."""


class AnalysisUsage(Protocol):
    def reserve(self) -> int:
        """Atomically consume one attempt, returning the remaining daily allowance."""

    def remaining(self) -> int:
        """Read the current daily allowance without consuming an attempt."""
