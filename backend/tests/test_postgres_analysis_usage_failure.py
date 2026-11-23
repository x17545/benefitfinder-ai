"""Regression tests for PostgreSQL analysis usage failures."""

import unittest
from unittest.mock import MagicMock, patch

import psycopg

from benefitfinder.application.analysis_usage import (
    AnalysisUsageUnavailable,
    DailyAnalysisLimitExceeded,
)
from benefitfinder.infrastructure.database.repositories.analysis_usage import (
    PostgresAnalysisUsage,
)


class PostgresAnalysisUsageFailureTests(unittest.TestCase):
    DATABASE_URL = "postgresql:///fictional_test"

    def test_reserve_connection_timeout_fails_closed(self):
        with patch(
            "psycopg.connect",
            side_effect=psycopg.errors.ConnectionTimeout("PRIVATE connection"),
        ) as connect:
            with self.assertRaises(AnalysisUsageUnavailable) as caught:
                PostgresAnalysisUsage(self.DATABASE_URL).reserve()

        self.assertEqual(connect.call_count, 1)
        self.assertEqual(str(caught.exception), "analysis_usage_unavailable")
        self.assertNotIn("PRIVATE", str(caught.exception))

    def test_reserve_sql_failure_does_not_retry(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.execute.side_effect = psycopg.errors.QueryCanceled(
            "PRIVATE query failure"
        )

        with patch("psycopg.connect", return_value=connection) as connect:
            with self.assertRaises(AnalysisUsageUnavailable):
                PostgresAnalysisUsage(self.DATABASE_URL).reserve()

        self.assertEqual(connect.call_count, 1)
        self.assertEqual(connection.execute.call_count, 1)

    def test_reserve_success_consumes_one_attempt(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.execute.return_value.fetchone.return_value = (1,)

        with patch("psycopg.connect", return_value=connection) as connect:
            remaining = PostgresAnalysisUsage(self.DATABASE_URL).reserve()

        self.assertEqual(remaining, 99)
        self.assertEqual(connect.call_count, 1)
        self.assertTrue(connect.call_args.kwargs["autocommit"])

    def test_reserve_daily_limit_is_distinct_from_db_failure(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.execute.return_value.fetchone.return_value = None

        with patch("psycopg.connect", return_value=connection):
            with self.assertRaises(DailyAnalysisLimitExceeded):
                PostgresAnalysisUsage(self.DATABASE_URL).reserve()

    def test_remaining_connection_timeout_fails_closed(self):
        with patch(
            "psycopg.connect",
            side_effect=psycopg.errors.ConnectionTimeout("PRIVATE connection"),
        ) as connect:
            with self.assertRaises(AnalysisUsageUnavailable) as caught:
                PostgresAnalysisUsage(self.DATABASE_URL).remaining()

        self.assertEqual(connect.call_count, 1)
        self.assertEqual(str(caught.exception), "analysis_usage_unavailable")
        self.assertNotIn("PRIVATE", str(caught.exception))


    def test_reserve_connection_timeout_logs_safe_diagnostics(self):
        with patch(
            "psycopg.connect",
            side_effect=psycopg.errors.ConnectionTimeout(
                "PRIVATE database password"
            ),
        ):
            with self.assertLogs(
                "benefitfinder.analysis_usage", level="WARNING"
            ) as captured:
                with self.assertRaises(AnalysisUsageUnavailable):
                    PostgresAnalysisUsage(self.DATABASE_URL).reserve()

        log_text = "\n".join(captured.output)
        self.assertIn("analysis_usage status=failed", log_text)
        self.assertIn("operation=reserve", log_text)
        self.assertIn("stage=connect", log_text)
        self.assertIn("error_type=ConnectionTimeout", log_text)
        self.assertNotIn("PRIVATE", log_text)

    def test_remaining_connection_timeout_logs_safe_diagnostics(self):
        with patch(
            "psycopg.connect",
            side_effect=psycopg.errors.ConnectionTimeout(
                "PRIVATE database password"
            ),
        ):
            with self.assertLogs(
                "benefitfinder.analysis_usage", level="WARNING"
            ) as captured:
                with self.assertRaises(AnalysisUsageUnavailable):
                    PostgresAnalysisUsage(self.DATABASE_URL).remaining()

        log_text = "\n".join(captured.output)
        self.assertIn("analysis_usage status=failed", log_text)
        self.assertIn("operation=remaining", log_text)
        self.assertIn("stage=connect", log_text)
        self.assertIn("error_type=ConnectionTimeout", log_text)
        self.assertNotIn("PRIVATE", log_text)

if __name__ == "__main__":
    unittest.main()
