"""Development admission races and Korean-midnight boundaries; no profiles."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from unittest import TestCase

from benefitfinder.application.analysis_usage import (
    AnalysisUsageUnavailable,
    DailyAnalysisLimitExceeded,
)
from benefitfinder.infrastructure.memory_analysis_usage import MemoryAnalysisUsage


class MemoryAnalysisUsageTests(TestCase):
    def test_one_hundred_atomic_admissions_even_under_concurrent_requests(self):
        usage = MemoryAnalysisUsage()

        def admit(_):
            try:
                return usage.reserve()
            except DailyAnalysisLimitExceeded:
                return None

        with ThreadPoolExecutor(max_workers=16) as executor:
            results = list(executor.map(admit, range(160)))
        self.assertEqual(sorted(value for value in results if value is not None), list(range(100)))
        self.assertEqual(results.count(None), 60)
        self.assertEqual(usage.remaining(), 0)

    def test_rollover_is_korean_midnight_and_reading_does_not_consume(self):
        now = datetime(2026, 11, 11, 14, 59, 59, tzinfo=timezone.utc)
        usage = MemoryAnalysisUsage(clock=lambda: now)
        self.assertEqual(usage.reserve(), 99)
        self.assertEqual(usage.remaining(), 99)
        self.assertEqual(usage.remaining(), 99)
        now = datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc)
        self.assertEqual(usage.remaining(), 100)
        self.assertEqual(usage.reserve(), 99)
        # A clock correction must not reopen an already used day's allowance.
        now = datetime(2026, 11, 11, 14, 59, 59, tzinfo=timezone.utc)
        self.assertEqual(usage.remaining(), 99)

    def test_naive_clock_is_not_silently_treated_as_local_timezone(self):
        usage = MemoryAnalysisUsage(clock=lambda: datetime(2026, 11, 11))
        with self.assertRaises(AnalysisUsageUnavailable):
            usage.reserve()
