"""DB-free checks for adapter configuration, budgets and source metadata propagation."""

import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from benefitfinder.bootstrap import create_app
from benefitfinder.infrastructure.database.repositories.published_clauses import (
    PostgresPublishedClauses,
)
from benefitfinder.infrastructure.example_explanation_data import recommendation_explanation_data


class PostgresClauseConfigurationTest(unittest.IsolatedAsyncioTestCase):
    async def test_bad_configuration_is_safe_and_explicit_injection_is_unambiguous(self) -> None:
        for value in ("", "PRIVATE_INVALID_VALUE", "host=localhost"):
            with self.assertRaisesRegex(ValueError, "^invalid_f13_database_url$"):
                PostgresPublishedClauses(value)
        with self.assertRaisesRegex(ValueError, "ambiguous_explanation_repository"):
            create_app(
                explanation_repository=AsyncMock(),
                explanation_database_url="postgresql:///example_test",
            )

    async def test_search_is_parameterized_bounded_and_preserves_source_metadata(self) -> None:
        request = recommendation_explanation_data()[0][0]
        checked_at = datetime(2026, 11, 7, tzinfo=timezone.utc)
        connection = AsyncMock()
        connection.__aenter__.return_value = connection
        cursor = AsyncMock()
        connection.execute.return_value = cursor
        cursor.fetchall.return_value = [
            {
                "clause_id": "id",
                "policy_id": request.policy_id,
                "document_version": request.document_version,
                "title": "가상 조항",
                "body": "가상 문장",
                "source_location": "가상 문서 2장",
                "topics": ["application"],
                "source_url": "https://example.invalid/source",
                "checked_at": checked_at,
            }
        ]
        with patch(
            "psycopg.AsyncConnection.connect", new_callable=AsyncMock, return_value=connection
        ) as connect:
            repo = PostgresPublishedClauses("postgresql:///fictional_test")
            rows = await repo.search(request, limit=5)
        options = connect.call_args.kwargs
        self.assertIn("default_transaction_read_only=on", options["options"])
        self.assertIn("statement_timeout=1000", options["options"])
        self.assertEqual(options["connect_timeout"], 2)
        params = connection.execute.call_args.args[1]
        self.assertEqual(
            params[:3], (request.policy_id, request.document_version, request.category)
        )
        self.assertEqual(params[-1], 5)
        self.assertEqual(rows[0].checked_at, checked_at)
        self.assertEqual(rows[0].location, "가상 문서 2장")
        connection.__aexit__.assert_awaited_once()
        for value in (0, 6, True):
            with self.assertRaisesRegex(ValueError, "invalid_clause_search_limit"):
                await repo.search(request, limit=value)

    async def test_db_errors_do_not_expose_connection_details(self) -> None:
        import psycopg

        with patch(
            "psycopg.AsyncConnection.connect",
            new_callable=AsyncMock,
            side_effect=psycopg.OperationalError("PRIVATE_DSN_AND_PASSWORD"),
        ):
            repo = PostgresPublishedClauses("postgresql:///fictional_test")
            with self.assertRaisesRegex(RuntimeError, "^published_clause_lookup_failed$"):
                await repo.search(recommendation_explanation_data()[0][0], limit=5)
