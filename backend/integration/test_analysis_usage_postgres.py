"""PostgreSQL daily admission checks in a disposable fictional test database."""

from __future__ import annotations

import os
import json
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from uuid import uuid4
from fastapi.testclient import TestClient

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from benefitfinder.application.analysis_usage import (
    DAILY_ANALYSIS_LIMIT,
    AnalysisUsageUnavailable,
    DailyAnalysisLimitExceeded,
)
from benefitfinder.infrastructure.database.repositories.analysis_usage import (
    PostgresAnalysisUsage,
)
from benefitfinder.bootstrap import create_app

_MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations/versions"
_MIGRATION_0001 = _MIGRATIONS / "0001_f13_published_clauses.sql"
_MIGRATION_0003 = _MIGRATIONS / "0003_analysis_usage.sql"
_UNAVAILABLE = "analysis_usage_unavailable"


def _base_database_url() -> str:
    url = os.environ.get("F13_TEST_DATABASE_URL", "")
    try:
        name = conninfo_to_dict(url).get("dbname", "")
    except (psycopg.Error, ValueError):
        raise ValueError("invalid_F13_TEST_DATABASE_URL") from None
    if not url or not isinstance(name, str) or not name.endswith("_test"):
        raise ValueError("F13_TEST_DATABASE_URL_must_target_dedicated_test_database")
    return url


def _apply_migration(url: str, path: Path) -> None:
    with psycopg.connect(url) as connection:
        connection.execute(path.read_text(encoding="utf-8"))


class PostgresAnalysisUsageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base_url = _base_database_url()
        cls.database_name = f"bf_usage_{uuid4().hex[:12]}_test"
        with psycopg.connect(cls.base_url, autocommit=True) as connection:
            connection.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(cls.database_name))
            )
        cls.url = make_conninfo(cls.base_url, dbname=cls.database_name)
        try:
            _apply_migration(cls.url, _MIGRATION_0001)
            _apply_migration(cls.url, _MIGRATION_0003)
        except BaseException:
            cls._drop_database()
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        cls._drop_database()

    @classmethod
    def _drop_database(cls) -> None:
        with psycopg.connect(cls.base_url, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(cls.database_name))
            )

    def setUp(self) -> None:
        with psycopg.connect(self.url) as connection:
            connection.execute("TRUNCATE public.analysis_daily_usage")

    def _execute(self, statement: str, params: tuple[object, ...] = ()) -> None:
        with psycopg.connect(self.url) as connection:
            connection.execute(statement, params)

    def test_meta_reads_shared_postgres_remaining_without_reserving(self) -> None:
        usage = PostgresAnalysisUsage(self.url)
        usage.reserve()
        with TestClient(create_app(analysis_usage_database_url=self.url)) as first:
            with TestClient(create_app(analysis_usage_database_url=self.url)) as second:
                self.assertEqual(first.get("/api/v1/meta").json()["daily_remaining"], 99)
                self.assertEqual(second.get("/api/v1/meta").json()["daily_remaining"], 99)
                usage.reserve()
                self.assertEqual(first.get("/api/v1/meta").json()["daily_remaining"], 98)
        self.assertEqual(PostgresAnalysisUsage(self.url).remaining(), 98)

    def test_concurrent_separate_instances_admit_exactly_limit(self) -> None:
        start = threading.Event()

        def attempt() -> int | None:
            usage = PostgresAnalysisUsage(self.url)
            start.wait(timeout=5)
            try:
                return usage.reserve()
            except DailyAnalysisLimitExceeded:
                return None

        with ThreadPoolExecutor(max_workers=16) as executor:
            futures = [executor.submit(attempt) for _ in range(125)]
            start.set()
            results = [future.result(timeout=30) for future in futures]

        admitted = sorted(result for result in results if result is not None)
        self.assertEqual(admitted, list(range(DAILY_ANALYSIS_LIMIT)))
        self.assertEqual(results.count(None), 25)
        self.assertEqual(PostgresAnalysisUsage(self.url).remaining(), 0)
        with psycopg.connect(self.url) as connection:
            self.assertEqual(
                connection.execute("SELECT used FROM public.analysis_daily_usage").fetchall(),
                [(DAILY_ANALYSIS_LIMIT,)],
            )

    def test_restart_persistence_and_remaining_does_not_consume(self) -> None:
        before = PostgresAnalysisUsage(self.url)
        self.assertEqual(before.remaining(), DAILY_ANALYSIS_LIMIT)
        self.assertEqual(before.remaining(), DAILY_ANALYSIS_LIMIT)
        with psycopg.connect(self.url) as connection:
            self.assertEqual(
                connection.execute("SELECT count(*) FROM public.analysis_daily_usage").fetchone(),
                (0,),
            )

        self.assertEqual(before.reserve(), DAILY_ANALYSIS_LIMIT - 1)
        restarted = PostgresAnalysisUsage(self.url)
        self.assertEqual(restarted.remaining(), DAILY_ANALYSIS_LIMIT - 1)
        self.assertEqual(restarted.reserve(), DAILY_ANALYSIS_LIMIT - 2)
        with psycopg.connect(self.url) as connection:
            self.assertEqual(
                connection.execute("SELECT used FROM public.analysis_daily_usage").fetchone(), (2,)
            )

    def test_two_api_instances_share_recommendation_and_simulation_allowance(self) -> None:
        self._execute(
            "INSERT INTO public.analysis_daily_usage(day,used) VALUES "
            "((CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Seoul')::date,98)"
        )
        root = Path(__file__).resolve().parents[2]
        recommendation = json.loads(
            (root / "contracts/fixtures/recommendation-request.json").read_text()
        )
        simulation = json.loads((root / "contracts/fixtures/simulation-request.json").read_text())
        with (
            TestClient(create_app(analysis_usage_database_url=self.url)) as first,
            TestClient(create_app(analysis_usage_database_url=self.url)) as second,
        ):
            self.assertEqual(
                first.post("/api/v1/recommendations", json=recommendation).status_code, 200
            )
            self.assertEqual(second.post("/api/v1/simulations", json=simulation).status_code, 200)
            rejected = first.post("/api/v1/recommendations", json=recommendation)
            self.assertEqual(rejected.status_code, 429)
            self.assertEqual(rejected.json()["code"], "daily_limit_exceeded")
        self.assertEqual(PostgresAnalysisUsage(self.url).remaining(), 0)

    def test_restricted_writer_can_count_without_reading_policy_documents(self) -> None:
        role = f"bf_usage_writer_{uuid4().hex[:10]}"
        with psycopg.connect(self.base_url, autocommit=True) as admin:
            admin.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'fictional-test-only'").format(
                    sql.Identifier(role)
                )
            )
        try:
            with psycopg.connect(self.url) as admin:
                admin.execute(
                    sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role))
                )
                admin.execute(
                    sql.SQL(
                        "GRANT SELECT,INSERT,UPDATE ON public.analysis_daily_usage TO {}"
                    ).format(sql.Identifier(role))
                )
            writer_url = make_conninfo(self.url, user=role, password="fictional-test-only")
            writer = PostgresAnalysisUsage(writer_url)
            self.assertEqual(writer.reserve(), 99)
            self.assertEqual(writer.remaining(), 99)
            with psycopg.connect(writer_url) as restricted:
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    restricted.execute("SELECT * FROM public.f13_policy_documents")
        finally:
            with psycopg.connect(self.url) as admin:
                admin.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            with psycopg.connect(self.base_url, autocommit=True) as admin:
                admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))

    def test_previous_kst_day_does_not_count(self) -> None:
        with psycopg.connect(self.url) as connection:
            connection.execute(
                """INSERT INTO public.analysis_daily_usage (day, used)
                   VALUES ((CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Seoul')::date - 1, %s)""",
                (DAILY_ANALYSIS_LIMIT,),
            )

        usage = PostgresAnalysisUsage(self.url)
        self.assertEqual(usage.remaining(), DAILY_ANALYSIS_LIMIT)
        self.assertEqual(usage.reserve(), DAILY_ANALYSIS_LIMIT - 1)
        with psycopg.connect(self.url) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT used FROM public.analysis_daily_usage ORDER BY day"
                ).fetchall(),
                [(DAILY_ANALYSIS_LIMIT,), (1,)],
            )

    def test_migration_atomic_reapply_and_constraints(self) -> None:
        with psycopg.connect(self.url, autocommit=True) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT version FROM public.benefitfinder_schema_migrations ORDER BY version"
                ).fetchall(),
                [("0001_f13_published_clauses",), ("0003_analysis_usage",)],
            )
            self.assertEqual(
                connection.execute(
                    """SELECT column_name, data_type, is_nullable
                       FROM information_schema.columns
                       WHERE table_schema='public' AND table_name='analysis_daily_usage'
                       ORDER BY ordinal_position"""
                ).fetchall(),
                [("day", "date", "NO"), ("used", "integer", "NO")],
            )
            with self.assertRaises(psycopg.errors.DuplicateTable), connection.transaction():
                connection.execute(_MIGRATION_0003.read_text(encoding="utf-8"))
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM public.benefitfinder_schema_migrations"
                ).fetchone(),
                (2,),
            )

        self._execute(
            "INSERT INTO public.analysis_daily_usage (day, used) VALUES (%s, 1)",
            (date(2026, 11, 12),),
        )
        with self.assertRaises(psycopg.errors.CheckViolation):
            self._execute("UPDATE public.analysis_daily_usage SET used=0")
        with self.assertRaises(psycopg.errors.CheckViolation):
            self._execute("UPDATE public.analysis_daily_usage SET used=101")
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self._execute(
                "INSERT INTO public.analysis_daily_usage (day, used) VALUES (%s, 2)",
                (date(2026, 11, 12),),
            )

    def test_failed_connection_uses_fixed_safe_errors(self) -> None:
        unavailable_url = (
            "postgresql://postgres:fictional-local-test-only@127.0.0.1:1/benefitfinder_usage_test"
        )
        usage = PostgresAnalysisUsage(unavailable_url)
        for operation in (usage.reserve, usage.remaining):
            with self.assertRaises(AnalysisUsageUnavailable) as raised:
                operation()
            self.assertEqual(str(raised.exception), _UNAVAILABLE)
            self.assertNotIn("postgresql://", str(raised.exception))
            self.assertNotIn("fictional-local-test-only", str(raised.exception))
            self.assertNotIn("analysis_daily_usage", str(raised.exception))

        with self.assertRaisesRegex(ValueError, "^invalid_analysis_usage_database_url$"):
            PostgresAnalysisUsage("")

    def test_permission_failure_fails_closed_without_details(self) -> None:
        role = f"bf_usage_denied_{uuid4().hex[:10]}"
        password = "fictional-test-only"
        with psycopg.connect(self.base_url, autocommit=True) as admin:
            admin.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal(password)
                )
            )
        try:
            denied_url = make_conninfo(self.url, user=role, password=password)
            usage = PostgresAnalysisUsage(denied_url)
            for operation in (usage.reserve, usage.remaining):
                with self.assertRaises(AnalysisUsageUnavailable) as raised:
                    operation()
                self.assertEqual(str(raised.exception), _UNAVAILABLE)
                self.assertNotIn(role, str(raised.exception))
                self.assertNotIn(password, str(raised.exception))
        finally:
            with psycopg.connect(self.base_url, autocommit=True) as admin:
                admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))

    def test_lock_timeout_fails_closed_without_details(self) -> None:
        usage = PostgresAnalysisUsage(self.url)
        with psycopg.connect(self.url) as locker:
            locker.execute("LOCK TABLE public.analysis_daily_usage IN ACCESS EXCLUSIVE MODE")
            started = time.monotonic()
            for operation in (usage.reserve, usage.remaining):
                with self.assertRaises(AnalysisUsageUnavailable) as raised:
                    operation()
                self.assertEqual(str(raised.exception), _UNAVAILABLE)
                self.assertNotIn("analysis_daily_usage", str(raised.exception))
        self.assertLess(time.monotonic() - started, 3)


if __name__ == "__main__":
    unittest.main()
