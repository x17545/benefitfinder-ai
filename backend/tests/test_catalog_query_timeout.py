"""Regression tests for published catalog query timeouts."""

import unittest
from unittest.mock import MagicMock, patch

import psycopg

from benefitfinder.application.published_policies import PolicyCatalogUnavailable
from benefitfinder.infrastructure.database.repositories.published_policies import (
    PostgresPublishedPolicies,
)
from benefitfinder.infrastructure.database.repositories.published_services import (
    PostgresPublishedServices,
)


class CatalogQueryTimeoutTests(unittest.TestCase):
    DATABASE_URL = "postgresql:///fictional_test"

    def test_published_policy_query_options(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.execute.return_value.fetchmany.return_value = []

        with patch("psycopg.connect", return_value=connection) as connect:
            result = PostgresPublishedPolicies(self.DATABASE_URL).list_published()

        self.assertEqual(result, ())
        self.assertEqual(connect.call_args.kwargs["connect_timeout"], 2)

        options = connect.call_args.kwargs["options"]
        self.assertIn("default_transaction_read_only=on", options)
        self.assertIn("statement_timeout=5000", options)
        self.assertIn("lock_timeout=500", options)
        self.assertIn("timezone=UTC", options)

    def test_published_service_query_options(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.execute.return_value.fetchmany.return_value = []

        with patch("psycopg.connect", return_value=connection) as connect:
            result = PostgresPublishedServices(self.DATABASE_URL).list_published()

        self.assertEqual(result, ())
        self.assertEqual(connect.call_args.kwargs["connect_timeout"], 2)

        options = connect.call_args.kwargs["options"]
        self.assertIn("default_transaction_read_only=on", options)
        self.assertIn("statement_timeout=5000", options)
        self.assertIn("lock_timeout=500", options)
        self.assertIn("timezone=UTC", options)

    def test_policy_query_failure_remains_unavailable(self):
        with patch(
            "psycopg.connect",
            side_effect=psycopg.OperationalError("private-db-error"),
        ):
            with self.assertRaises(PolicyCatalogUnavailable) as caught:
                PostgresPublishedPolicies(self.DATABASE_URL).list_published()

        self.assertEqual(str(caught.exception), "policy_catalog_lookup_failed")
        self.assertNotIn("private-db-error", str(caught.exception))

    def test_service_query_failure_remains_unavailable(self):
        with patch(
            "psycopg.connect",
            side_effect=psycopg.OperationalError("private-db-error"),
        ):
            with self.assertRaises(PolicyCatalogUnavailable) as caught:
                PostgresPublishedServices(self.DATABASE_URL).list_published()

        self.assertEqual(str(caught.exception), "service_catalog_lookup_failed")
        self.assertNotIn("private-db-error", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
