"""Real PostgreSQL checks for bounded collected-policy generation staging."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import unittest
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from benefitfinder.application.collected_policies import (
    CollectedPolicyBatch,
    CollectedPolicyStoreError,
    CollectionImportResult,
)
from benefitfinder.application.collection import CollectedRecord
from benefitfinder.infrastructure.database.repositories.collected_policies import (
    PostgresCollectedPolicies,
)


MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations/versions"
MIGRATION_NAMES = (
    "0001_f13_published_clauses.sql",
    "0002_published_policy_catalog.sql",
    "0005_collected_policy_generations.sql",
)
STORAGE_UNAVAILABLE = "collected_policy_storage_unavailable"


@contextmanager
def _dedicated_database(base: str) -> Iterator[str]:
    """Create and remove one fictional-only database selected by the test."""
    name = "bf_staging_" + uuid4().hex + "_test"
    with psycopg.connect(base, autocommit=True) as connection:
        connection.execute(
            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name))
        )
    url = make_conninfo(base, dbname=name)
    try:
        with psycopg.connect(url) as connection:
            for migration in MIGRATION_NAMES:
                connection.execute((MIGRATIONS / migration).read_text())
        yield url
    finally:
        with psycopg.connect(base, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(name)
                )
            )


class PostgresCollectedPolicyStagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base = os.environ.get("F13_TEST_DATABASE_URL", "")
        if (
            not cls.base
            or not conninfo_to_dict(cls.base).get("dbname", "").endswith("_test")
        ):
            raise ValueError("dedicated_test_database_required")
        cls.database = _dedicated_database(cls.base)
        cls.url = cls.database.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.database.__exit__(None, None, None)

    def setUp(self) -> None:
        self.checked_at = datetime(2026, 11, 17, 1, 0, tzinfo=timezone.utc)
        with psycopg.connect(self.url) as connection:
            connection.execute(
                """
                DROP TRIGGER IF EXISTS bf_staging_chunk_failure
                ON public.collected_policy_changes
                """
            )
            connection.execute(
                "DROP FUNCTION IF EXISTS public.bf_staging_chunk_failure()"
            )
            connection.execute(
                """
                TRUNCATE public.collected_policy_changes,
                         public.collected_policies,
                         public.collected_policy_runs,
                         public.f13_clauses,
                         public.f13_policy_documents,
                         public.policy_catalog_releases
                """
            )
            connection.execute(
                """
                INSERT INTO public.f13_policy_documents
                    (policy_id, document_version, policy_name, category,
                     source_url, checked_at, publication_status, published_at)
                VALUES ('youth:fictional-staging', 'v1', '가상 게시 정책',
                        'employment', 'https://example.test/fictional',
                        %s, 'published', %s)
                """,
                (self.checked_at, self.checked_at),
            )
            connection.execute(
                """
                INSERT INTO public.f13_clauses
                    (policy_id, document_version, clause_id, title, body,
                     source_location, topics, review_status, reviewed_by,
                     reviewed_at, publication_status, published_at)
                VALUES ('youth:fictional-staging', 'v1', 'clause-1',
                        '가상 조건', '가상 게시 자료입니다.',
                        '가상 안내 / 1', ARRAY['employment'], 'approved',
                        'fictional-reviewer', %s, 'published', %s)
                """,
                (self.checked_at, self.checked_at),
            )
            payload = {
                "benefit": {
                    "id": "youth:fictional-staging",
                    "source": {"document_version": "v1"},
                }
            }
            connection.execute(
                """
                INSERT INTO public.policy_catalog_releases
                    (policy_id, document_version, payload, approved_sha256,
                     review_status, reviewed_by, reviewed_at,
                     publication_status, published_at)
                VALUES ('youth:fictional-staging', 'v1', %s::jsonb, %s,
                        'approved', 'fictional-reviewer', %s,
                        'published', %s)
                """,
                (
                    json.dumps(payload),
                    hashlib.sha256(b"fictional-approved").hexdigest(),
                    self.checked_at,
                    self.checked_at,
                ),
            )
        self.repository = PostgresCollectedPolicies(self.url)
        self.published_snapshot = self._published_snapshot()

    @staticmethod
    def _record(
        index: int,
        *,
        completed_at: datetime,
        generation: int,
        external_id: str | None = None,
    ) -> CollectedRecord:
        record_id = external_id or f"chunk-{index:03d}"
        material = f"fictional-material-{index:03d}"
        return CollectedRecord(
            source_name="youthcenter",
            external_id=record_id,
            record_sha256=hashlib.sha256(material.encode("utf-8")).hexdigest(),
            snapshot_ref=(
                f"fictional-snapshot://youthcenter/{record_id}/generation-{generation}"
            ),
            fetched_at=completed_at - timedelta(seconds=1),
            fields={
                "fictional_index": index,
                "fictional_generation": generation,
                "fictional_text": "가상 수집 자료",
            },
        )

    @staticmethod
    def _batch(
        records: tuple[CollectedRecord, ...],
        completed_at: datetime,
        *,
        run_id: str | None = None,
    ) -> CollectedPolicyBatch:
        return CollectedPolicyBatch(
            source_name="youthcenter",
            run_id=run_id or uuid4().hex,
            completed_at=completed_at,
            records=records,
        )

    def _assert_result(
        self,
        result: CollectionImportResult,
        *,
        new: int,
        changed: int,
        unchanged: int,
        missing: int,
        replayed: bool = False,
    ) -> None:
        self.assertEqual(
            (result.new, result.changed, result.unchanged, result.missing, result.replayed),
            (new, changed, unchanged, missing, replayed),
        )

    def _storage_snapshot(self) -> tuple[object, object, object]:
        with psycopg.connect(self.url) as connection:
            runs = connection.execute(
                """
                SELECT source, run_id, completed_at, batch_sha256, record_count,
                       new_count, changed_count, unchanged_count, missing_count
                FROM public.collected_policy_runs
                ORDER BY source, run_id
                """
            ).fetchall()
            policies = connection.execute(
                """
                SELECT source, external_id, material_sha256, snapshot_ref,
                       fetched_at, fields, last_run_id, missing_detected_at
                FROM public.collected_policies
                ORDER BY source, external_id
                """
            ).fetchall()
            changes = connection.execute(
                """
                SELECT source, run_id, external_id, kind, previous_sha256,
                       current_sha256, record_payload
                FROM public.collected_policy_changes
                ORDER BY source, run_id, external_id
                """
            ).fetchall()
        return runs, policies, changes

    def _published_snapshot(self) -> tuple[object, object, object]:
        with psycopg.connect(self.url) as connection:
            documents = connection.execute(
                """
                SELECT policy_id, document_version, policy_name, category,
                       source_url, checked_at, publication_status, published_at
                FROM public.f13_policy_documents
                ORDER BY policy_id, document_version
                """
            ).fetchall()
            clauses = connection.execute(
                """
                SELECT policy_id, document_version, clause_id, title, body,
                       source_location, topics, review_status, reviewed_by,
                       reviewed_at, publication_status, published_at
                FROM public.f13_clauses
                ORDER BY policy_id, document_version, clause_id
                """
            ).fetchall()
            catalog = connection.execute(
                """
                SELECT policy_id, document_version, payload, approved_sha256,
                       review_status, reviewed_by, reviewed_at,
                       publication_status, published_at
                FROM public.policy_catalog_releases
                ORDER BY policy_id, document_version
                """
            ).fetchall()
        return documents, clauses, catalog

    def test_500_rows_refresh_and_chunk_boundary_missing_reappearance(self) -> None:
        first_at = datetime(2026, 11, 17, 3, 0, tzinfo=timezone.utc)
        first = self._batch(
            tuple(
                self._record(index, completed_at=first_at, generation=1)
                for index in range(500)
            ),
            first_at,
        )
        self._assert_result(
            self.repository.save(first),
            new=500,
            changed=0,
            unchanged=0,
            missing=0,
        )

        refresh_at = first_at + timedelta(hours=1)
        refresh = self._batch(
            tuple(
                self._record(index, completed_at=refresh_at, generation=2)
                for index in range(500)
            ),
            refresh_at,
        )
        self._assert_result(
            self.repository.save(refresh),
            new=0,
            changed=0,
            unchanged=500,
            missing=0,
        )
        self._assert_result(
            PostgresCollectedPolicies(self.url).save(refresh),
            new=0,
            changed=0,
            unchanged=500,
            missing=0,
            replayed=True,
        )

        missing_at = refresh_at + timedelta(hours=1)
        across_missing = self._batch(
            tuple(
                self._record(index, completed_at=missing_at, generation=3)
                for index in range(500)
                if index not in {199, 200}
            ),
            missing_at,
        )
        self._assert_result(
            self.repository.save(across_missing),
            new=0,
            changed=0,
            unchanged=498,
            missing=2,
        )

        reappeared_at = missing_at + timedelta(hours=1)
        reappeared = self._batch(
            tuple(
                self._record(index, completed_at=reappeared_at, generation=4)
                for index in range(500)
                if index not in {200, 398}
            ),
            reappeared_at,
        )
        self._assert_result(
            self.repository.save(reappeared),
            new=1,
            changed=0,
            unchanged=497,
            missing=1,
        )
        self._assert_result(
            self.repository.save(reappeared),
            new=1,
            changed=0,
            unchanged=497,
            missing=1,
            replayed=True,
        )

        with psycopg.connect(self.url) as connection:
            runs = connection.execute(
                """
                SELECT run_id, record_count, new_count, changed_count,
                       unchanged_count, missing_count
                FROM public.collected_policy_runs
                ORDER BY completed_at
                """
            ).fetchall()
            policies = connection.execute(
                """
                SELECT external_id, last_run_id, missing_detected_at, fields
                FROM public.collected_policies
                ORDER BY external_id
                """
            ).fetchall()
            boundary_changes = connection.execute(
                """
                SELECT change.run_id, change.external_id, change.kind
                FROM public.collected_policy_changes AS change
                JOIN public.collected_policy_runs AS run
                  ON run.source = change.source AND run.run_id = change.run_id
                WHERE change.run_id = ANY(%s)
                ORDER BY run.completed_at, change.external_id
                """,
                ([across_missing.run_id, reappeared.run_id],),
            ).fetchall()
            change_count = connection.execute(
                "SELECT count(*) FROM public.collected_policy_changes"
            ).fetchone()[0]

        self.assertEqual(len(runs), 4)
        self.assertEqual(tuple(runs[0][1:]), (500, 500, 0, 0, 0))
        self.assertEqual(tuple(runs[1][1:]), (500, 0, 0, 500, 0))
        self.assertEqual(tuple(runs[2][1:]), (498, 0, 0, 498, 2))
        self.assertEqual(tuple(runs[3][1:]), (498, 1, 0, 497, 1))
        self.assertEqual(len(policies), 500)

        by_id = {row[0]: row for row in policies}
        self.assertEqual(by_id["chunk-199"][1], reappeared.run_id)
        self.assertIsNone(by_id["chunk-199"][2])
        self.assertEqual(by_id["chunk-199"][3]["fictional_generation"], 4)
        self.assertEqual(by_id["chunk-200"][1], refresh.run_id)
        self.assertEqual(by_id["chunk-200"][2], missing_at)
        self.assertEqual(by_id["chunk-398"][1], across_missing.run_id)
        self.assertEqual(by_id["chunk-398"][2], reappeared_at)
        self.assertEqual(
            boundary_changes,
            [
                (across_missing.run_id, "chunk-199", "missing"),
                (across_missing.run_id, "chunk-200", "missing"),
                (reappeared.run_id, "chunk-199", "new"),
                (reappeared.run_id, "chunk-398", "missing"),
            ],
        )
        self.assertEqual(change_count, 504)
        self.assertEqual(self._published_snapshot(), self.published_snapshot)

    def test_second_chunk_failure_rolls_back_runs_policies_and_changes(self) -> None:
        first_at = datetime(2026, 11, 17, 6, 0, tzinfo=timezone.utc)
        first = self._batch(
            tuple(
                self._record(index, completed_at=first_at, generation=1)
                for index in range(2)
            ),
            first_at,
        )
        self._assert_result(
            self.repository.save(first),
            new=2,
            changed=0,
            unchanged=0,
            missing=0,
        )
        before = self._storage_snapshot()

        failing_at = first_at + timedelta(hours=1)
        failing_records = []
        for index in range(500):
            external_id = (
                "chunk-250-chunk2-failure" if index == 250 else None
            )
            failing_records.append(
                self._record(
                    index,
                    completed_at=failing_at,
                    generation=2,
                    external_id=external_id,
                )
            )
        # Position 250 is inside the second bounded 200-row staging chunk.
        failing = self._batch(tuple(failing_records), failing_at)

        with psycopg.connect(self.url) as connection:
            connection.execute(
                """
                CREATE OR REPLACE FUNCTION public.bf_staging_chunk_failure()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    IF NEW.external_id = 'chunk-250-chunk2-failure' THEN
                        RAISE EXCEPTION 'fictional chunk failure'
                            USING ERRCODE = '23514';
                    END IF;
                    RETURN NEW;
                END
                $$
                """
            )
            connection.execute(
                """
                CREATE TRIGGER bf_staging_chunk_failure
                BEFORE INSERT ON public.collected_policy_changes
                FOR EACH ROW EXECUTE FUNCTION public.bf_staging_chunk_failure()
                """
            )

        try:
            with self.assertRaises(CollectedPolicyStoreError) as raised:
                self.repository.save(failing)
            self.assertEqual(str(raised.exception), STORAGE_UNAVAILABLE)
            self.assertEqual(self._storage_snapshot(), before)
            with psycopg.connect(self.url) as connection:
                latest = connection.execute(
                    """
                    SELECT run_id FROM public.collected_policy_runs
                    ORDER BY completed_at DESC LIMIT 1
                    """
                ).fetchone()[0]
                policies = connection.execute(
                    """
                    SELECT external_id, last_run_id
                    FROM public.collected_policies
                    ORDER BY external_id
                    """
                ).fetchall()
            self.assertEqual(latest, first.run_id)
            self.assertEqual(
                policies,
                [("chunk-000", first.run_id), ("chunk-001", first.run_id)],
            )
            self.assertEqual(self._published_snapshot(), self.published_snapshot)
        finally:
            with psycopg.connect(self.url) as connection:
                connection.execute(
                    """
                    DROP TRIGGER IF EXISTS bf_staging_chunk_failure
                    ON public.collected_policy_changes
                    """
                )
                connection.execute(
                    "DROP FUNCTION IF EXISTS public.bf_staging_chunk_failure()"
                )

        self._assert_result(
            self.repository.save(failing),
            new=498,
            changed=0,
            unchanged=2,
            missing=0,
        )
        self.assertEqual(self._published_snapshot(), self.published_snapshot)


if __name__ == "__main__":
    unittest.main()
