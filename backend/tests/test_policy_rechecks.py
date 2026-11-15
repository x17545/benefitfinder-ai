"""DB-free checks for the read-only policy recheck page reader."""

import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import psycopg
from psycopg.rows import dict_row

from benefitfinder.infrastructure.database.repositories.policy_rechecks import (
    list_policy_rechecks,
)


def _timestamp(day: int = 12, hour: int = 3) -> datetime:
    return datetime(2026, 11, day, hour, 30, tzinfo=timezone.utc)


def _row(task_id: int, *, reason: str = "source_changed") -> dict[str, object]:
    return {
        "task_id": task_id,
        "policy_id": f"fictional-policy-{task_id}",
        "document_version": f"v{task_id}",
        "reason": reason,
        "source": None if reason == "source_unlinked" else "youthcenter",
        "external_id": None if reason == "source_unlinked" else str(task_id),
        "run_id": None if reason == "source_unlinked" else f"run-{task_id}",
        "approved_material_sha256": "a" * 64,
        "current_material_sha256": "b" * 64,
        "detected_at": _timestamp(day=task_id),
        "approved_sha256": "c" * 64,
        "reviewed_by": None,
        "reviewed_at": None,
        "invalidated_document_versions": [f"v{task_id}"],
        "latest_material_sha256": "d" * 64,
        "latest_missing_detected_at": None,
        "latest_run_id": f"latest-run-{task_id}",
        "superseded_by_version": None,
    }


def _mock_connection(rows: list[dict[str, object]]) -> tuple[MagicMock, MagicMock]:
    connection = MagicMock()
    connection.__enter__.return_value = connection
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchall.return_value = rows
    connection.cursor.return_value = cursor
    return connection, cursor


class PolicyRechecksTest(unittest.TestCase):
    def test_uses_read_only_bounded_query_and_returns_lookahead_cursor(self) -> None:
        connection, cursor = _mock_connection([_row(4), _row(5), _row(6)])
        with patch("psycopg.connect", return_value=connection) as connect:
            result = list_policy_rechecks(
                "postgresql:///fictional_test", after_id=3, limit=2
            )

        options = connect.call_args.kwargs
        self.assertEqual(options["connect_timeout"], 2)
        self.assertIs(options["row_factory"], dict_row)
        self.assertIn("default_transaction_read_only=on", options["options"])
        self.assertIn("statement_timeout=2000", options["options"])
        self.assertIn("lock_timeout=500", options["options"])
        self.assertIn("timezone=UTC", options["options"])

        statement, params = cursor.execute.call_args.args
        self.assertTrue(statement.lstrip().upper().startswith("SELECT"))
        self.assertIn("LEFT JOIN public.collected_policies", statement)
        self.assertIn("WHERE q.task_id > %s", statement)
        self.assertIn("ORDER BY q.task_id ASC", statement)
        self.assertIn("LIMIT %s", statement)
        self.assertEqual(params, (3, 3))
        self.assertEqual(result["format_version"], 1)
        self.assertEqual(result["kind"], "policy_recheck_page")
        self.assertEqual([item["task_id"] for item in result["items"]], [4, 5])
        self.assertEqual(result["next_after_id"], 5)
        connection.__enter__.assert_called_once_with()
        connection.__exit__.assert_called_once()

    def test_legacy_unlinked_row_keeps_nulls_and_serializes_aware_times_as_utc(self) -> None:
        row = _row(1, reason="source_unlinked")
        row["detected_at"] = datetime(
            2026, 11, 12, 12, 30, tzinfo=timezone(timedelta(hours=9))
        )
        row["reviewed_at"] = datetime(2026, 11, 12, 14, 0, tzinfo=timezone.utc)
        row["latest_material_sha256"] = None
        row["latest_missing_detected_at"] = None
        row["latest_run_id"] = None
        row["raw_text"] = "PRIVATE_ROW_CONTENT"
        row["profile"] = {"private": "PRIVATE_PROFILE_CONTENT"}
        connection, _ = _mock_connection([row])

        with patch("psycopg.connect", return_value=connection):
            result = list_policy_rechecks("postgresql:///fictional_test")

        item = result["items"][0]
        self.assertIsNone(item["source"])
        self.assertIsNone(item["external_id"])
        self.assertIsNone(item["run_id"])
        self.assertIsNone(item["latest_material_sha256"])
        self.assertIsNone(item["latest_missing_detected_at"])
        self.assertIsNone(item["latest_run_id"])
        self.assertEqual(item["detected_at"], "2026-11-12T03:30:00+00:00")
        self.assertEqual(item["reviewed_at"], "2026-11-12T14:00:00+00:00")
        self.assertNotIn("raw_text", item)
        self.assertNotIn("profile", item)
        self.assertEqual(result["next_after_id"], None)
        self.assertNotIn("PRIVATE", json.dumps(result, ensure_ascii=False))

    def test_empty_page_has_no_next_cursor(self) -> None:
        connection, _ = _mock_connection([])
        with patch("psycopg.connect", return_value=connection):
            result = list_policy_rechecks("postgresql:///fictional_test")
        self.assertEqual(result["items"], [])
        self.assertIsNone(result["next_after_id"])

    def test_connection_and_row_failures_use_one_safe_error(self) -> None:
        with patch(
            "psycopg.connect",
            side_effect=psycopg.OperationalError("PRIVATE_DSN_AND_PASSWORD"),
        ):
            with self.assertRaises(RuntimeError) as raised:
                list_policy_rechecks("postgresql://user:private@/fictional_test")
        self.assertEqual(str(raised.exception), "policy_recheck_lookup_failed")
        self.assertNotIn("PRIVATE", str(raised.exception))

        invalid_row = _row(2)
        invalid_row["detected_at"] = "PRIVATE_ROW_CONTENT"
        connection, _ = _mock_connection([invalid_row])
        with patch("psycopg.connect", return_value=connection):
            with self.assertRaises(RuntimeError) as raised:
                list_policy_rechecks("postgresql:///fictional_test")
        self.assertEqual(str(raised.exception), "policy_recheck_lookup_failed")
        self.assertNotIn("PRIVATE", str(raised.exception))

    def test_rejects_non_monotonic_or_duplicate_task_ids(self) -> None:
        for rows in ([_row(2), _row(1)], [_row(2), _row(2)]):
            connection, _ = _mock_connection(rows)
            with patch("psycopg.connect", return_value=connection):
                with self.assertRaisesRegex(RuntimeError, "^policy_recheck_lookup_failed$"):
                    list_policy_rechecks("postgresql:///fictional_test")

    def test_rejects_invalid_arguments_and_database_urls_before_connecting(self) -> None:
        with patch("psycopg.connect") as connect:
            for after_id in (-1, 1.0, "1", True, 2**63):
                with self.assertRaisesRegex(ValueError, "^invalid_policy_recheck_after_id$"):
                    list_policy_rechecks(
                        "postgresql:///fictional_test", after_id=after_id
                    )
            for limit in (0, 1001, 1.0, "1", True):
                with self.assertRaisesRegex(ValueError, "^invalid_policy_recheck_limit$"):
                    list_policy_rechecks("postgresql:///fictional_test", limit=limit)
            for database_url in (None, "", "   ", "PRIVATE_INVALID_VALUE", "host=localhost"):
                with self.assertRaisesRegex(
                    ValueError, "^invalid_policy_recheck_database_url$"
                ):
                    list_policy_rechecks(database_url)
        connect.assert_not_called()
