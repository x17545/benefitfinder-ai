"""Real PostgreSQL checks for complete, unreviewed collected-policy generations."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
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
LOCK_NAME = "benefitfinder:collected_policy_generations:youthcenter"
STORAGE_UNAVAILABLE = "collected_policy_storage_unavailable"


class PostgresCollectedPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base = os.environ.get("F13_TEST_DATABASE_URL", "")
        if (
            not cls.base
            or not conninfo_to_dict(cls.base).get("dbname", "").endswith("_test")
        ):
            raise ValueError("dedicated_test_database_required")
        cls.name = "bf_collected_" + uuid4().hex + "_test"
        with psycopg.connect(cls.base, autocommit=True) as connection:
            connection.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(cls.name))
            )
        cls.url = make_conninfo(cls.base, dbname=cls.name)
        try:
            with psycopg.connect(cls.url) as connection:
                for name in (
                    "0001_f13_published_clauses.sql",
                    "0002_published_policy_catalog.sql",
                    "0005_collected_policy_generations.sql",
                ):
                    connection.execute((MIGRATIONS / name).read_text())
        except BaseException:
            cls._drop_database()
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        cls._drop_database()

    @classmethod
    def _drop_database(cls) -> None:
        with psycopg.connect(cls.base, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(cls.name)
                )
            )

    def setUp(self) -> None:
        self.checked_at = datetime(2026, 11, 12, 0, 30, tzinfo=timezone.utc)
        with psycopg.connect(self.url) as connection:
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
                VALUES ('youth:fictional-collected', 'v1', '가상 게시 정책',
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
                VALUES ('youth:fictional-collected', 'v1', 'clause-1',
                        '가상 조건', '가상 게시 자료입니다.',
                        '가상 안내 / 1', ARRAY['employment'], 'approved',
                        'fictional-reviewer', %s, 'published', %s)
                """,
                (self.checked_at, self.checked_at),
            )
            payload = {
                "benefit": {
                    "id": "youth:fictional-collected",
                    "source": {"document_version": "v1"},
                }
            }
            connection.execute(
                """
                INSERT INTO public.policy_catalog_releases
                    (policy_id, document_version, payload, approved_sha256,
                     review_status, reviewed_by, reviewed_at,
                     publication_status, published_at)
                VALUES ('youth:fictional-collected', 'v1', %s::jsonb, %s,
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
        external_id: str,
        material: str,
        *,
        completed_at: datetime,
        views: int = 1,
        snapshot_ref: str | None = None,
    ) -> CollectedRecord:
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
        return CollectedRecord(
            source_name="youthcenter",
            external_id=external_id,
            record_sha256=digest,
            snapshot_ref=snapshot_ref
            or f"fictional-snapshot://youthcenter/{external_id}/{material}",
            fetched_at=completed_at - timedelta(minutes=1),
            fields={
                "external_id": external_id,
                "material": material,
                "views": views,
            },
        )

    @classmethod
    def _batch(
        cls,
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

    def test_db_diff_updates_unchanged_metadata_and_replays_without_rollback(self) -> None:
        first_at = datetime(2026, 11, 12, 1, 0, tzinfo=timezone.utc)
        first = self._batch(
            (
                self._record("A", "a-v1", completed_at=first_at, views=1),
                self._record("B", "b-v1", completed_at=first_at, views=1),
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

        second_at = first_at + timedelta(hours=1)
        second = self._batch(
            (
                self._record(
                    "A",
                    "a-v1",
                    completed_at=second_at,
                    views=99,
                    snapshot_ref="fictional-snapshot://youthcenter/A/a-v1-refreshed",
                ),
                self._record("B", "b-v2", completed_at=second_at, views=2),
                self._record("C", "c-v1", completed_at=second_at),
            ),
            second_at,
        )
        self._assert_result(
            self.repository.save(second),
            new=1,
            changed=1,
            unchanged=1,
            missing=0,
        )

        with psycopg.connect(self.url) as connection:
            rows = connection.execute(
                """
                SELECT external_id, material_sha256, snapshot_ref, fields,
                       last_run_id, missing_detected_at, fetched_at
                FROM public.collected_policies
                ORDER BY external_id
                """
            ).fetchall()
            changes = connection.execute(
                """
                SELECT change.run_id, change.external_id, change.kind
                FROM public.collected_policy_changes AS change
                JOIN public.collected_policy_runs AS run
                  ON run.source = change.source AND run.run_id = change.run_id
                ORDER BY run.completed_at, change.external_id
                """
            ).fetchall()
            b_payloads = connection.execute(
                """
                SELECT change.kind, change.previous_sha256, change.current_sha256,
                       change.record_payload
                FROM public.collected_policy_changes AS change
                JOIN public.collected_policy_runs AS run
                  ON run.source = change.source AND run.run_id = change.run_id
                WHERE change.source = 'youthcenter'
                  AND change.external_id = 'B'
                ORDER BY run.completed_at
                """
            ).fetchall()
            run = connection.execute(
                """
                SELECT run_id, record_count, new_count, changed_count,
                       unchanged_count, missing_count
                FROM public.collected_policy_runs
                WHERE run_id = %s
                """,
                (second.run_id,),
            ).fetchone()
        self.assertEqual(
            rows[0][2],
            "fictional-snapshot://youthcenter/A/a-v1-refreshed",
        )
        self.assertEqual(rows[0][3]["views"], 99)
        self.assertEqual(rows[0][4], second.run_id)
        self.assertEqual(rows[0][5], None)
        self.assertEqual(rows[0][6], second_at - timedelta(minutes=1))
        self.assertEqual(
            changes,
            [
                (first.run_id, "A", "new"),
                (first.run_id, "B", "new"),
                (second.run_id, "B", "changed"),
                (second.run_id, "C", "new"),
            ],
        )
        self.assertEqual(tuple(run[1:]), (3, 1, 1, 1, 0))
        self.assertEqual(b_payloads[0][0], "new")
        self.assertEqual(b_payloads[0][3]["fields"]["material"], "b-v1")
        self.assertEqual(b_payloads[1][0], "changed")
        self.assertEqual(
            b_payloads[1][1],
            hashlib.sha256(b"b-v1").hexdigest(),
        )
        self.assertEqual(
            b_payloads[1][2],
            hashlib.sha256(b"b-v2").hexdigest(),
        )
        self.assertEqual(b_payloads[1][3]["fields"]["material"], "b-v2")

        restarted = PostgresCollectedPolicies(self.url)
        self._assert_result(
            restarted.save(second),
            new=1,
            changed=1,
            unchanged=1,
            missing=0,
            replayed=True,
        )
        self._assert_result(
            restarted.save(first),
            new=2,
            changed=0,
            unchanged=0,
            missing=0,
            replayed=True,
        )

        conflicting = self._batch(
            (self._record("A", "different-content", completed_at=first_at),),
            first_at,
            run_id=first.run_id,
        )
        with self.assertRaises(CollectedPolicyStoreError) as raised:
            restarted.save(conflicting)
        self.assertEqual(str(raised.exception), "collected_policy_run_conflict")

        with psycopg.connect(self.url) as connection:
            latest = connection.execute(
                """
                SELECT run_id FROM public.collected_policy_runs
                WHERE source = 'youthcenter'
                ORDER BY completed_at DESC LIMIT 1
                """
            ).fetchone()[0]
            a_hash = connection.execute(
                """
                SELECT material_sha256 FROM public.collected_policies
                WHERE source = 'youthcenter' AND external_id = 'A'
                """
            ).fetchone()[0]
        self.assertEqual(latest, second.run_id)
        self.assertEqual(a_hash, hashlib.sha256(b"a-v1").hexdigest())
        self.assertEqual(self._published_snapshot(), self.published_snapshot)

    def test_empty_generation_marks_missing_and_reappearance_is_new(self) -> None:
        first_at = datetime(2026, 11, 12, 2, 0, tzinfo=timezone.utc)
        first = self._batch(
            (
                self._record("A", "a-v1", completed_at=first_at),
                self._record("B", "b-v1", completed_at=first_at),
            ),
            first_at,
        )
        self.repository.save(first)

        empty_at = first_at + timedelta(hours=1)
        empty = self._batch((), empty_at)
        self._assert_result(
            self.repository.save(empty),
            new=0,
            changed=0,
            unchanged=0,
            missing=2,
        )
        with psycopg.connect(self.url) as connection:
            rows = connection.execute(
                """
                SELECT external_id, material_sha256, fields, missing_detected_at
                FROM public.collected_policies
                ORDER BY external_id
                """
            ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row[3] == empty_at for row in rows))
        self.assertEqual(rows[1][2]["material"], "b-v1")

        reappeared_at = empty_at + timedelta(hours=1)
        reappeared = self._batch(
            (self._record("B", "b-v1", completed_at=reappeared_at),),
            reappeared_at,
        )
        self._assert_result(
            self.repository.save(reappeared),
            new=1,
            changed=0,
            unchanged=0,
            missing=0,
        )
        with psycopg.connect(self.url) as connection:
            policy = connection.execute(
                """
                SELECT last_run_id, missing_detected_at
                FROM public.collected_policies
                WHERE source = 'youthcenter' AND external_id = 'B'
                """
            ).fetchone()
            events = connection.execute(
                """
                SELECT change.run_id, change.kind, change.previous_sha256,
                       change.current_sha256, change.record_payload
                FROM public.collected_policy_changes AS change
                JOIN public.collected_policy_runs AS run
                  ON run.source = change.source AND run.run_id = change.run_id
                WHERE change.source = 'youthcenter'
                  AND change.external_id = 'B'
                ORDER BY run.completed_at
                """
            ).fetchall()
        self.assertEqual(policy, (reappeared.run_id, None))
        self.assertEqual(len(events), 3)
        self.assertEqual(events[1][1], "missing")
        self.assertIsNone(events[1][3])
        self.assertEqual(events[1][4]["fields"]["material"], "b-v1")
        self.assertEqual(events[2][1], "new")
        self.assertIsNone(events[2][2])
        self.assertEqual(events[2][3], hashlib.sha256(b"b-v1").hexdigest())

    def test_late_and_equal_completion_times_never_replace_latest(self) -> None:
        latest_at = datetime(2026, 11, 12, 4, 0, tzinfo=timezone.utc)
        latest = self._batch(
            (self._record("A", "a-latest", completed_at=latest_at),),
            latest_at,
        )
        self.repository.save(latest)

        for late_at in (
            latest_at - timedelta(seconds=1),
            latest_at,
        ):
            with self.subTest(completed_at=late_at):
                late = self._batch(
                    (self._record("A", "a-old", completed_at=late_at),),
                    late_at,
                )
                with self.assertRaises(CollectedPolicyStoreError) as raised:
                    PostgresCollectedPolicies(self.url).save(late)
                self.assertEqual(
                    str(raised.exception),
                    "collected_policy_generation_regressed",
                )

        with psycopg.connect(self.url) as connection:
            runs = connection.execute(
                "SELECT run_id FROM public.collected_policy_runs"
            ).fetchall()
            material = connection.execute(
                """
                SELECT material_sha256 FROM public.collected_policies
                WHERE source = 'youthcenter' AND external_id = 'A'
                """
            ).fetchone()[0]
        self.assertEqual(runs, [(latest.run_id,)])
        self.assertEqual(material, hashlib.sha256(b"a-latest").hexdigest())

    def test_database_failure_rolls_back_the_whole_generation(self) -> None:
        first_at = datetime(2026, 11, 12, 5, 0, tzinfo=timezone.utc)
        first = self._batch(
            (self._record("A", "a-v1", completed_at=first_at),),
            first_at,
        )
        self.repository.save(first)
        before = self._storage_snapshot()

        with psycopg.connect(self.url) as connection:
            connection.execute(
                """
                CREATE OR REPLACE FUNCTION public.bf_force_collected_failure()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    IF NEW.external_id = 'FAIL' THEN
                        RAISE EXCEPTION 'fictional trigger failure';
                    END IF;
                    RETURN NEW;
                END
                $$
                """
            )
            connection.execute(
                """
                CREATE TRIGGER bf_collected_failure
                BEFORE INSERT ON public.collected_policy_changes
                FOR EACH ROW EXECUTE FUNCTION public.bf_force_collected_failure()
                """
            )

        second_at = first_at + timedelta(hours=1)
        failing = self._batch(
            (
                self._record("A", "a-v2", completed_at=second_at),
                self._record("FAIL", "fail-v1", completed_at=second_at),
            ),
            second_at,
        )
        try:
            with self.assertRaises(CollectedPolicyStoreError) as raised:
                self.repository.save(failing)
            self.assertEqual(str(raised.exception), STORAGE_UNAVAILABLE)
            self.assertEqual(self._storage_snapshot(), before)
        finally:
            with psycopg.connect(self.url) as connection:
                connection.execute(
                    """
                    DROP TRIGGER IF EXISTS bf_collected_failure
                    ON public.collected_policy_changes
                    """
                )
                connection.execute(
                    "DROP FUNCTION IF EXISTS public.bf_force_collected_failure()"
                )

        self._assert_result(
            self.repository.save(failing),
            new=1,
            changed=1,
            unchanged=0,
            missing=0,
        )
        self.assertEqual(self._published_snapshot(), self.published_snapshot)

    def test_sql_constraints_and_migration_history_guard_invariants(self) -> None:
        at = datetime(2026, 11, 12, 6, 0, tzinfo=timezone.utc)
        baseline = self._batch(
            (self._record("A", "a-v1", completed_at=at),),
            at,
        )
        self.repository.save(baseline)
        valid_hash = hashlib.sha256(b"valid").hexdigest()
        valid_payload = {
            "source_name": "youthcenter",
            "external_id": "B",
            "record_sha256": valid_hash,
            "snapshot_ref": "fictional-snapshot://youthcenter/B/valid",
            "fetched_at": (at - timedelta(minutes=1)).isoformat(),
            "fields": {"external_id": "B"},
        }
        with psycopg.connect(self.url) as connection:
            connection.execute(
                """
                INSERT INTO public.collected_policies
                    (source, external_id, material_sha256, snapshot_ref,
                     fetched_at, fields, last_run_id, missing_detected_at)
                VALUES ('youthcenter', 'B', %s,
                        'fictional-snapshot://youthcenter/B/valid',
                        %s, '{"external_id":"B"}'::jsonb, %s, NULL)
                """,
                (valid_hash, at - timedelta(minutes=1), baseline.run_id),
            )
        snapshot_before = self._storage_snapshot()

        invalid_statements = [
            (
                """
                INSERT INTO public.collected_policy_runs
                    (source, run_id, completed_at, batch_sha256, record_count,
                     new_count, changed_count, unchanged_count, missing_count)
                VALUES ('other', %s, %s, %s, 0, 0, 0, 0, 0)
                """,
                (uuid4().hex, at + timedelta(days=1), valid_hash),
            ),
            (
                """
                INSERT INTO public.collected_policy_runs
                    (source, run_id, completed_at, batch_sha256, record_count,
                     new_count, changed_count, unchanged_count, missing_count)
                VALUES ('youthcenter', %s, %s, 'not-a-hash', 0, 0, 0, 0, 0)
                """,
                (uuid4().hex, at + timedelta(days=2)),
            ),
            (
                """
                INSERT INTO public.collected_policy_runs
                    (source, run_id, completed_at, batch_sha256, record_count,
                     new_count, changed_count, unchanged_count, missing_count)
                VALUES ('youthcenter', %s, %s, %s, 1, 0, 0, 0, 0)
                """,
                (uuid4().hex, at + timedelta(days=3), valid_hash),
            ),
            (
                """
                INSERT INTO public.collected_policy_runs
                    (source, run_id, completed_at, batch_sha256, record_count,
                     new_count, changed_count, unchanged_count, missing_count)
                VALUES ('youthcenter', %s, %s, %s, 0, 0, 0, 0, 0)
                """,
                (uuid4().hex, at, valid_hash),
            ),
            (
                "UPDATE public.collected_policies SET fields = '[]'::jsonb",
                (),
            ),
            (
                """
                UPDATE public.collected_policies
                SET missing_detected_at = fetched_at - interval '1 second'
                """,
                (),
            ),
            (
                """
                UPDATE public.collected_policy_changes
                SET kind = 'unchanged'
                """,
                (),
            ),
            (
                """
                UPDATE public.collected_policy_changes
                SET previous_sha256 = %s
                """,
                (valid_hash,),
            ),
            (
                """
                UPDATE public.collected_policy_changes
                SET record_payload = '[]'::jsonb
                """,
                (),
            ),
            (
                """
                UPDATE public.collected_policy_changes
                SET record_payload = jsonb_set(
                    record_payload, '{fields}', '[]'::jsonb
                )
                """,
                (),
            ),
            (
                """
                UPDATE public.collected_policy_changes
                SET record_payload = record_payload - 'fields'
                """,
                (),
            ),
            *[
                (
                    """
                    INSERT INTO public.collected_policy_changes
                        (source, run_id, external_id, kind, previous_sha256,
                         current_sha256, record_payload)
                    VALUES ('youthcenter', %s, 'B', 'new', NULL, %s,
                            jsonb_set(%s::jsonb, %s::text[], 'null'::jsonb))
                    """,
                    (
                        baseline.run_id,
                        valid_hash,
                        json.dumps(valid_payload),
                        [key],
                    ),
                )
                for key in (
                    "source_name",
                    "external_id",
                    "record_sha256",
                    "snapshot_ref",
                    "fetched_at",
                )
            ],
            (
                """
                INSERT INTO public.collected_policy_changes
                    (source, run_id, external_id, kind, previous_sha256,
                     current_sha256, record_payload)
                VALUES ('youthcenter', %s, 'B', 'new', NULL, %s,
                        jsonb_set(%s::jsonb, '{snapshot_ref}', '"   "'::jsonb))
                """,
                (baseline.run_id, valid_hash, json.dumps(valid_payload)),
            ),
            (
                """
                UPDATE public.collected_policy_changes
                SET current_sha256 = NULL
                """,
                (),
            ),
        ]
        for index, (statement, params) in enumerate(invalid_statements):
            with self.subTest(index=index), self.assertRaises(psycopg.Error):
                with psycopg.connect(self.url) as connection:
                    connection.execute(statement, params)

        with psycopg.connect(self.url) as connection:
            version = connection.execute(
                """
                SELECT version FROM public.benefitfinder_schema_migrations
                WHERE version = '0005_collected_policy_generations'
                """
            ).fetchone()
        self.assertEqual(version, ("0005_collected_policy_generations",))
        with self.assertRaises(psycopg.Error):
            with psycopg.connect(self.url) as connection:
                connection.execute(
                    (MIGRATIONS / "0005_collected_policy_generations.sql").read_text()
                )
        self.assertEqual(self._storage_snapshot(), snapshot_before)

    def test_mutable_fields_serialization_errors_are_sanitized(self) -> None:
        at = datetime(2026, 11, 12, 6, 30, tzinfo=timezone.utc)
        batch = self._batch(
            (self._record("A", "a-v1", completed_at=at),),
            at,
        )
        recursive: dict[str, object] = {}
        recursive["next"] = recursive
        batch.records[0].fields["recursive"] = recursive

        with self.assertRaises(CollectedPolicyStoreError) as raised:
            self.repository.save(batch)
        self.assertEqual(str(raised.exception), STORAGE_UNAVAILABLE)

        recursion_batch = self._batch(
            (self._record("A", "a-v1", completed_at=at + timedelta(minutes=1)),),
            at + timedelta(minutes=1),
        )
        with patch(
            "benefitfinder.infrastructure.database.repositories."
            "collected_policies._incoming_records_json",
            side_effect=RecursionError,
        ), self.assertRaises(CollectedPolicyStoreError) as raised:
            self.repository.save(recursion_batch)
        self.assertEqual(str(raised.exception), STORAGE_UNAVAILABLE)

        with psycopg.connect(self.url) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM public.collected_policy_runs"
                ).fetchone()[0],
                0,
            )

    def test_advisory_lock_times_out_and_concurrent_instances_do_not_reorder(self) -> None:
        first_at = datetime(2026, 11, 12, 7, 0, tzinfo=timezone.utc)
        blocked = self._batch(
            (self._record("A", "blocked", completed_at=first_at),),
            first_at,
        )
        with psycopg.connect(self.url) as holder:
            holder.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (LOCK_NAME,),
            )
            with self.assertRaises(CollectedPolicyStoreError) as raised:
                PostgresCollectedPolicies(self.url).save(blocked)
            self.assertEqual(str(raised.exception), STORAGE_UNAVAILABLE)
            holder.rollback()
        with psycopg.connect(self.url) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM public.collected_policy_runs"
                ).fetchone()[0],
                0,
            )
        self.repository.save(blocked)

        lower_at = first_at + timedelta(hours=1)
        higher_at = first_at + timedelta(hours=2)
        lower = self._batch(
            (self._record("A", "lower", completed_at=lower_at),),
            lower_at,
        )
        higher = self._batch(
            (self._record("A", "higher", completed_at=higher_at),),
            higher_at,
        )
        barrier = threading.Barrier(2)

        def run(batch: CollectedPolicyBatch) -> object:
            barrier.wait()
            try:
                return PostgresCollectedPolicies(self.url).save(batch)
            except CollectedPolicyStoreError as error:
                return error

        with ThreadPoolExecutor(max_workers=2) as executor:
            lower_result, higher_result = tuple(
                executor.map(run, (lower, higher))
            )

        if isinstance(higher_result, CollectedPolicyStoreError):
            higher_result = self.repository.save(higher)
        self.assertIsInstance(higher_result, CollectionImportResult)
        if isinstance(lower_result, CollectedPolicyStoreError):
            with self.assertRaises(CollectedPolicyStoreError) as raised:
                self.repository.save(lower)
            self.assertEqual(
                str(raised.exception),
                "collected_policy_generation_regressed",
            )

        with psycopg.connect(self.url) as connection:
            latest = connection.execute(
                """
                SELECT run_id FROM public.collected_policy_runs
                ORDER BY completed_at DESC LIMIT 1
                """
            ).fetchone()[0]
            material = connection.execute(
                """
                SELECT material_sha256 FROM public.collected_policies
                WHERE source = 'youthcenter' AND external_id = 'A'
                """
            ).fetchone()[0]
        self.assertEqual(latest, higher.run_id)
        self.assertEqual(material, hashlib.sha256(b"higher").hexdigest())

    def test_writer_reads_nothing_else_and_reader_cannot_write(self) -> None:
        suffix = uuid4().hex[:16]
        writer = "bf_cp_writer_" + suffix
        reader = "bf_cp_reader_" + suffix
        password = "fictional-test-only"
        try:
            with psycopg.connect(self.base, autocommit=True) as connection:
                for role in (writer, reader):
                    connection.execute(
                        sql.SQL(
                            "CREATE ROLE {} LOGIN PASSWORD {} "
                            "NOSUPERUSER NOCREATEDB NOCREATEROLE"
                        ).format(
                            sql.Identifier(role),
                            sql.Literal(password),
                        )
                    )
                    connection.execute(
                        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                            sql.Identifier(self.name),
                            sql.Identifier(role),
                        )
                    )
                connection.execute(
                    sql.SQL("GRANT TEMP ON DATABASE {} TO {}").format(
                        sql.Identifier(self.name),
                        sql.Identifier(writer),
                    )
                )

            with psycopg.connect(self.url) as connection:
                for role in (writer, reader):
                    connection.execute(
                        sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(
                            sql.Identifier(role)
                        )
                    )
                    connection.execute(
                        sql.SQL(
                            "GRANT SELECT ON public.collected_policy_runs, "
                            "public.collected_policies, "
                            "public.collected_policy_changes TO {}"
                        ).format(sql.Identifier(role))
                    )
                connection.execute(
                    sql.SQL(
                        "GRANT INSERT ON public.collected_policy_runs, "
                        "public.collected_policy_changes TO {}"
                    ).format(sql.Identifier(writer))
                )
                connection.execute(
                    sql.SQL(
                        "GRANT INSERT, UPDATE ON public.collected_policies TO {}"
                    ).format(sql.Identifier(writer))
                )

            writer_url = make_conninfo(self.url, user=writer, password=password)
            reader_url = make_conninfo(self.url, user=reader, password=password)
            at = datetime(2026, 11, 12, 8, 0, tzinfo=timezone.utc)
            writer_batch = self._batch(
                (self._record("A", "writer", completed_at=at),),
                at,
            )
            self._assert_result(
                PostgresCollectedPolicies(writer_url).save(writer_batch),
                new=1,
                changed=0,
                unchanged=0,
                missing=0,
            )
            with psycopg.connect(reader_url) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT count(*) FROM public.collected_policies"
                    ).fetchone()[0],
                    1,
                )
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with psycopg.connect(writer_url) as connection:
                    connection.execute(
                        "SELECT count(*) FROM public.f13_policy_documents"
                    )

            reader_batch = self._batch(
                (self._record("B", "reader", completed_at=at + timedelta(hours=1)),),
                at + timedelta(hours=1),
            )
            with self.assertRaises(CollectedPolicyStoreError) as raised:
                PostgresCollectedPolicies(reader_url).save(reader_batch)
            self.assertEqual(str(raised.exception), STORAGE_UNAVAILABLE)
            self.assertEqual(self._published_snapshot(), self.published_snapshot)
        finally:
            with psycopg.connect(self.base, autocommit=True) as connection:
                roles = {
                    row[0]
                    for row in connection.execute(
                        "SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)",
                        ([writer, reader],),
                    ).fetchall()
                }
            if roles:
                with psycopg.connect(self.url) as connection:
                    for role in roles:
                        connection.execute(
                            sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role))
                        )
            with psycopg.connect(self.base, autocommit=True) as connection:
                for role in roles:
                    connection.execute(
                        sql.SQL("REVOKE ALL ON DATABASE {} FROM {}").format(
                            sql.Identifier(self.name),
                            sql.Identifier(role),
                        )
                    )
                    connection.execute(
                        sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role))
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


if __name__ == "__main__":
    unittest.main()
