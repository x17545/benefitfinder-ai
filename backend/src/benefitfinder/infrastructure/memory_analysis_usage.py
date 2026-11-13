"""Single-process development counter; production uses the shared PostgreSQL adapter."""

from collections.abc import Callable
from datetime import date, datetime, timezone
from threading import Lock
from zoneinfo import ZoneInfo

from benefitfinder.application.analysis_usage import (
    DAILY_ANALYSIS_LIMIT,
    AnalysisUsageUnavailable,
    DailyAnalysisLimitExceeded,
)

KST = ZoneInfo("Asia/Seoul")


class MemoryAnalysisUsage:
    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._counts: dict[date, int] = {}
        self._lock = Lock()

    def _today(self) -> date:
        now = self._clock()
        if not isinstance(now, datetime) or now.utcoffset() is None:
            raise AnalysisUsageUnavailable
        return now.astimezone(KST).date()

    def reserve(self) -> int:
        with self._lock:
            today = self._today()
            used = self._counts.get(today, 0)
            if used >= DAILY_ANALYSIS_LIMIT:
                raise DailyAnalysisLimitExceeded
            self._counts[today] = used + 1
            return DAILY_ANALYSIS_LIMIT - used - 1

    def remaining(self) -> int:
        with self._lock:
            return DAILY_ANALYSIS_LIMIT - self._counts.get(self._today(), 0)
