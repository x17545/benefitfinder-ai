"""PostgreSQL-backed daily analysis admission shared by every worker."""

from __future__ import annotations

import logging
from time import perf_counter

import psycopg
from psycopg.conninfo import conninfo_to_dict

from benefitfinder.application.analysis_usage import (
    DAILY_ANALYSIS_LIMIT,
    AnalysisUsageUnavailable,
    DailyAnalysisLimitExceeded,
)

_KST_DAY = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Seoul')::date"
_RESERVE_SQL = f"""
INSERT INTO public.analysis_daily_usage AS usage (day, used)
VALUES ({_KST_DAY}, 1)
ON CONFLICT (day) DO UPDATE
SET used = usage.used + 1
WHERE usage.used < %s
RETURNING usage.used
"""
_REMAINING_SQL = f"""
SELECT used
FROM public.analysis_daily_usage
WHERE day = {_KST_DAY}
"""
_CONNECTION_OPTIONS = "-c statement_timeout=1000 -c lock_timeout=500 -c timezone=Asia/Seoul"
_APPLICATION_NAME = "benefitfinder-analysis-usage"
_UNAVAILABLE = "analysis_usage_unavailable"


class PostgresAnalysisUsage:
    """Admit analyses atomically within PostgreSQL's current Korean calendar day."""

    def __init__(self, database_url: str) -> None:
        try:
            if not database_url.strip() or not conninfo_to_dict(database_url).get("dbname"):
                raise ValueError
        except (psycopg.Error, ValueError):
            raise ValueError("invalid_analysis_usage_database_url") from None
        self._database_url = database_url

    def reserve(self) -> int:
        """Atomically consume one shared daily admission and return what remains."""
        logger = logging.getLogger("benefitfinder.analysis_usage")
        started = perf_counter()
        stage = "connect"
        try:
            # The UPSERT is one transaction: release the row lock on the server
            # without waiting for the client to receive the result and send COMMIT.
            with psycopg.connect(
                self._database_url,
                autocommit=True,
                connect_timeout=2,
                options=_CONNECTION_OPTIONS,
                application_name=_APPLICATION_NAME,
            ) as connection:
                stage = "query"
                row = connection.execute(_RESERVE_SQL, (DAILY_ANALYSIS_LIMIT,)).fetchone()
        except (TimeoutError, psycopg.Error, OSError) as error:
            logger.warning(
                "analysis_usage status=failed operation=reserve stage=%s error_type=%s elapsed_ms=%.1f",
                stage, type(error).__name__, (perf_counter() - started) * 1000,
            )
            raise AnalysisUsageUnavailable(_UNAVAILABLE) from None

        if row is None:
            raise DailyAnalysisLimitExceeded("daily_analysis_limit_exceeded")
        used = row[0]
        if type(used) is not int or not 1 <= used <= DAILY_ANALYSIS_LIMIT:
            raise AnalysisUsageUnavailable(_UNAVAILABLE)
        return DAILY_ANALYSIS_LIMIT - used

    def remaining(self) -> int:
        """Read the current shared daily allowance without consuming it."""
        logger = logging.getLogger("benefitfinder.analysis_usage")
        started = perf_counter()
        stage = "connect"
        try:
            with psycopg.connect(
                self._database_url,
                connect_timeout=2,
                options=_CONNECTION_OPTIONS,
                application_name=_APPLICATION_NAME,
            ) as connection:
                stage = "query"
                row = connection.execute(_REMAINING_SQL).fetchone()
        except (TimeoutError, psycopg.Error, OSError) as error:
            logger.warning(
                "analysis_usage status=failed operation=remaining stage=%s error_type=%s elapsed_ms=%.1f",
                stage, type(error).__name__, (perf_counter() - started) * 1000,
            )
            raise AnalysisUsageUnavailable(_UNAVAILABLE) from None

        if row is None:
            return DAILY_ANALYSIS_LIMIT
        used = row[0]
        if type(used) is not int or not 1 <= used <= DAILY_ANALYSIS_LIMIT:
            raise AnalysisUsageUnavailable(_UNAVAILABLE)
        return DAILY_ANALYSIS_LIMIT - used
