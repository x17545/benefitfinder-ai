"""Regression tests for published policy query diagnostics."""

import unittest
from unittest.mock import MagicMock, patch

from benefitfinder.infrastructure.database.repositories.published_policies import (
    PostgresPublishedPolicies,
)


class PublishedCatalogDiagnosticsTests(unittest.TestCase):
    DATABASE_URL = "postgresql:///fictional_test"

    def test_success_logs_safe_query_timings(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.execute.return_value.fetchmany.return_value = []

        with patch("psycopg.connect", return_value=connection):
            with self.assertLogs("benefitfinder.catalog", level="INFO") as captured:
                result = PostgresPublishedPolicies(
                    self.DATABASE_URL
                ).list_published()

        self.assertEqual(result, ())
        output = " ".join(captured.output)

        for field in (
            "connect_ms=",
            "query_ms=",
            "validation_ms=",
            "row_count=0",
        ):
            self.assertIn(field, output)

        self.assertNotIn(self.DATABASE_URL, output)


    def test_connection_failure_logs_stage_without_secrets(self):
        from benefitfinder.application.published_policies import PolicyCatalogUnavailable

        with patch(
            "psycopg.connect",
            side_effect=OSError("PRIVATE database password"),
        ):
            with self.assertLogs("benefitfinder.catalog", level="WARNING") as captured:
                with self.assertRaises(PolicyCatalogUnavailable):
                    PostgresPublishedPolicies(self.DATABASE_URL).list_published()

        output = " ".join(captured.output)
        self.assertIn("status=failed", output)
        self.assertIn("stage=connect", output)
        self.assertIn("error_type=OSError", output)
        self.assertNotIn("PRIVATE", output)
        self.assertNotIn(self.DATABASE_URL, output)


    def test_query_timeout_logs_stage_without_secrets(self):
        import psycopg

        from benefitfinder.application.published_policies import PolicyCatalogUnavailable

        connection = MagicMock()
        connection.__enter__.return_value = connection
        def execute(sql, params=()):
            if "SELECT payload, approved_sha256, published_at" in str(sql):
                raise psycopg.errors.QueryCanceled(
                    "PRIVATE statement timeout detail"
                )
            cursor = MagicMock()
            if "policy_catalog_revision" in str(sql):
                cursor.fetchone.return_value = {"revision": 1}
            return cursor

        connection.execute.side_effect = execute

        with patch("psycopg.connect", return_value=connection):
            with self.assertLogs("benefitfinder.catalog", level="WARNING") as captured:
                with self.assertRaises(PolicyCatalogUnavailable) as caught:
                    PostgresPublishedPolicies(self.DATABASE_URL).list_published()

        output = " ".join(captured.output)
        self.assertIn("status=failed", output)
        self.assertIn("stage=query", output)
        self.assertIn("error_type=QueryCanceled", output)
        self.assertNotIn("PRIVATE", output)
        self.assertNotIn(self.DATABASE_URL, output)
        self.assertEqual(str(caught.exception), "policy_catalog_lookup_failed")


if __name__ == "__main__":
    unittest.main()
