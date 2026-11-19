"""Regression tests for published catalog snapshot caching."""

import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from backend.tests.catalog_fixtures import release_data
from benefitfinder.infrastructure.database.repositories.published_policies import (
    PostgresPublishedPolicies,
)
from benefitfinder.infrastructure.policy_catalog_records import (
    CatalogRecord,
    record_hash,
)


class PublishedCatalogCacheContractTests(unittest.TestCase):
    DATABASE_URL = "postgresql:///fictional_test"

    def test_unchanged_revision_reuses_verified_snapshot(self):
        record = CatalogRecord.model_validate(release_data())
        digest = record_hash(record)
        published_at = datetime(2026, 11, 18, tzinfo=timezone.utc)

        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.execute.return_value.fetchone.return_value = {"revision": 1}
        connection.execute.return_value.fetchmany.return_value = [
            {
                "payload": record.model_dump(mode="json"),
                "approved_sha256": digest,
                "published_at": published_at,
            }
        ]

        repository = PostgresPublishedPolicies(self.DATABASE_URL)

        with patch("psycopg.connect", return_value=connection):
            first = repository.list_published()
            second = repository.list_published()

        self.assertEqual(len(first), 1)
        self.assertEqual(first, second)

        full_reads = [
            call for call in connection.execute.call_args_list
            if "SELECT payload, approved_sha256, published_at"
            in str(call.args[0])
        ]
        self.assertEqual(len(full_reads), 1)


    def test_database_failure_does_not_return_cached_snapshot(self):
        import psycopg

        from benefitfinder.application.published_policies import (
            PolicyCatalogUnavailable,
        )

        record = CatalogRecord.model_validate(release_data())
        published_at = datetime(2026, 11, 18, tzinfo=timezone.utc)

        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.execute.return_value.fetchone.return_value = {"revision": 1}
        connection.execute.return_value.fetchmany.return_value = [
            {
                "payload": record.model_dump(mode="json"),
                "approved_sha256": record_hash(record),
                "published_at": published_at,
            }
        ]

        repository = PostgresPublishedPolicies(self.DATABASE_URL)

        with patch("psycopg.connect", return_value=connection):
            self.assertEqual(len(repository.list_published()), 1)

        with patch(
            "psycopg.connect",
            side_effect=psycopg.OperationalError("database unavailable"),
        ):
            with self.assertRaises(PolicyCatalogUnavailable):
                repository.list_published()

    def test_changed_revision_refreshes_snapshot(self):
        record = CatalogRecord.model_validate(release_data())
        digest = record_hash(record)
        published_at = datetime(2026, 11, 18, tzinfo=timezone.utc)

        connection = MagicMock()
        connection.__enter__.return_value = connection

        revisions = iter([{"revision": 1}, {"revision": 2}])
        full_read_count = 0

        def execute(sql, params=()):
            nonlocal full_read_count
            cursor = MagicMock()

            if "catalog_revision" in str(sql):
                cursor.fetchone.return_value = next(revisions)
            elif "SELECT payload, approved_sha256, published_at" in str(sql):
                full_read_count += 1
                cursor.fetchmany.return_value = [{
                    "payload": record.model_dump(mode="json"),
                    "approved_sha256": digest,
                    "published_at": published_at,
                }]
            return cursor

        connection.execute.side_effect = execute

        repository = PostgresPublishedPolicies(self.DATABASE_URL)

        with patch("psycopg.connect", return_value=connection):
            repository.list_published()
            repository.list_published()

        self.assertEqual(full_read_count, 2)

if __name__ == "__main__":
    unittest.main()
