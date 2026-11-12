"""One last successful ECOS observation, shared by API workers and the offline job."""

from __future__ import annotations

from decimal import Decimal

import psycopg
from psycopg.conninfo import conninfo_to_dict

from benefitfinder.application.base_rates import BaseRateObservation, BaseRateUnavailable

_OPTIONS = "-c statement_timeout=1000 -c lock_timeout=500 -c timezone=UTC"
_UNAVAILABLE = "base_rate_storage_unavailable"


class PostgresBaseRates:
    def __init__(self, database_url: str) -> None:
        try:
            if not database_url.strip() or not conninfo_to_dict(database_url).get("dbname"):
                raise ValueError
        except (psycopg.Error, ValueError):
            raise ValueError("invalid_base_rate_database_url") from None
        self._database_url = database_url

    def latest(self) -> BaseRateObservation | None:
        try:
            with psycopg.connect(
                self._database_url, connect_timeout=2,
                options=_OPTIONS + " -c default_transaction_read_only=on",
                application_name="benefitfinder-base-rate-reader",
            ) as connection:
                row = connection.execute(
                    "SELECT annual_rate, observed_on, checked_at, stat_code, item_code, unit_name "
                    "FROM public.ecos_base_rate WHERE singleton = true"
                ).fetchone()
            if row is None:
                return None
            if row[3:] != ("722Y001", "0101000", "연%"):
                raise ValueError
            return BaseRateObservation(float(row[0]), row[1], row[2])
        except (psycopg.Error, OSError, ValueError, TypeError, OverflowError):
            raise BaseRateUnavailable(_UNAVAILABLE) from None

    def save(self, observation: BaseRateObservation) -> None:
        try:
            with psycopg.connect(
                self._database_url, connect_timeout=2, options=_OPTIONS,
                application_name="benefitfinder-base-rate-writer",
            ) as connection:
                row = connection.execute(
                    """INSERT INTO public.ecos_base_rate AS rate
                       (singleton, annual_rate, observed_on, checked_at) VALUES (true, %s, %s, %s)
                       ON CONFLICT (singleton) DO UPDATE SET
                       annual_rate = EXCLUDED.annual_rate, observed_on = EXCLUDED.observed_on,
                       checked_at = EXCLUDED.checked_at
                       WHERE EXCLUDED.observed_on >= rate.observed_on
                         AND EXCLUDED.checked_at >= rate.checked_at
                       RETURNING singleton""",
                    (Decimal(str(observation.annual_rate)), observation.observed_on,
                     observation.checked_at),
                ).fetchone()
                if row is None:
                    raise BaseRateUnavailable("base_rate_observation_regressed")
        except (psycopg.Error, OSError, ValueError, TypeError):
            raise BaseRateUnavailable(_UNAVAILABLE) from None
