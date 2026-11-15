"""Real PostgreSQL rate persistence in a disposable, fictional test database."""

from datetime import date, datetime, timezone
import os
from pathlib import Path
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from backend.tests.catalog_fixtures import MemoryPolicies, release_data
from benefitfinder.application.base_rates import BaseRateObservation, BaseRateUnavailable
from benefitfinder.bootstrap import create_app
from benefitfinder.infrastructure.database.repositories.base_rates import PostgresBaseRates

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations/versions"


class PostgresBaseRateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base = os.environ.get("F13_TEST_DATABASE_URL", "")
        if not cls.base or not conninfo_to_dict(cls.base).get("dbname", "").endswith("_test"):
            raise ValueError("dedicated_test_database_required")
        cls.name = "bf_rates_" + uuid4().hex[:12] + "_test"
        with psycopg.connect(cls.base, autocommit=True) as connection:
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(cls.name)))
        cls.url = make_conninfo(cls.base, dbname=cls.name)
        try:
            with psycopg.connect(cls.url) as connection:
                for name in ("0001_f13_published_clauses.sql", "0004_ecos_base_rate.sql"):
                    connection.execute((MIGRATIONS / name).read_text())
        except BaseException:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        with psycopg.connect(cls.base, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(cls.name)))

    def setUp(self) -> None:
        with psycopg.connect(self.url) as connection:
            connection.execute("TRUNCATE public.ecos_base_rate")
        self.repository = PostgresBaseRates(self.url)
        self.observation = BaseRateObservation(0.025, date(2026, 11, 1),
                                               datetime(2026, 11, 2, tzinfo=timezone.utc))

    def test_last_success_persists_across_instances_and_api_reads(self) -> None:
        self.assertIsNone(self.repository.latest())
        self.repository.save(self.observation)
        self.assertEqual(PostgresBaseRates(self.url).latest(), self.observation)
        with TestClient(create_app(
            policy_repository=MemoryPolicies(release_data()), base_rate_database_url=self.url,
        )) as client:
            response = client.get("/api/v1/meta")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["base_rate"], 0.025)
            self.assertEqual(response.json()["base_rate_status"], "verified")

    def test_delayed_refresh_never_overwrites_a_newer_observation(self) -> None:
        self.repository.save(self.observation)
        for old in (
            BaseRateObservation(0.02, date(2026, 10, 31), datetime(2026, 11, 3, tzinfo=timezone.utc)),
            BaseRateObservation(0.02, date(2026, 11, 1), datetime(2026, 11, 1, tzinfo=timezone.utc)),
        ):
            with self.assertRaises(BaseRateUnavailable):
                self.repository.save(old)
            self.assertEqual(self.repository.latest(), self.observation)
        corrected = BaseRateObservation(0.03, date(2026, 11, 1),
                                         datetime(2026, 11, 3, tzinfo=timezone.utc))
        self.repository.save(corrected)
        self.assertEqual(self.repository.latest(), corrected)

    def test_database_constraints_and_reapply_failure_preserve_the_existing_rate(self) -> None:
        self.repository.save(self.observation)
        for statement in (
            "UPDATE public.ecos_base_rate SET annual_rate=-1",
            "UPDATE public.ecos_base_rate SET annual_rate='NaN'",
            "UPDATE public.ecos_base_rate SET observed_on='2099-01-01'",
            "UPDATE public.ecos_base_rate SET stat_code='other'",
            "UPDATE public.ecos_base_rate SET singleton=false",
        ):
            with self.subTest(statement=statement), self.assertRaises(psycopg.Error):
                with psycopg.connect(self.url) as connection:
                    connection.execute(statement)
            self.assertEqual(self.repository.latest(), self.observation)
        with self.assertRaises(psycopg.Error):
            with psycopg.connect(self.url) as connection:
                connection.execute((MIGRATIONS / "0004_ecos_base_rate.sql").read_text())
        self.assertEqual(self.repository.latest(), self.observation)

    def test_writer_lock_failure_keeps_the_previous_success(self) -> None:
        self.repository.save(self.observation)
        with psycopg.connect(self.url) as locked:
            locked.execute("SELECT * FROM public.ecos_base_rate FOR UPDATE")
            newer = BaseRateObservation(0.03, date(2026, 11, 2),
                                        datetime(2026, 11, 3, tzinfo=timezone.utc))
            with self.assertRaises(BaseRateUnavailable) as raised:
                self.repository.save(newer)
            self.assertEqual(str(raised.exception), "base_rate_storage_unavailable")
        self.assertEqual(self.repository.latest(), self.observation)

    def test_read_role_cannot_refresh_or_access_policy_documents(self) -> None:
        self.repository.save(self.observation)
        role = "bf_rate_reader_" + uuid4().hex[:12]
        with psycopg.connect(self.base, autocommit=True) as connection:
            connection.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(role), sql.Literal("fictional-test-only"),
            ))
        try:
            with psycopg.connect(self.url) as connection:
                connection.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
                connection.execute(sql.SQL("GRANT SELECT ON public.ecos_base_rate TO {}").format(sql.Identifier(role)))
            restricted = make_conninfo(self.url, user=role, password="fictional-test-only")
            reader = PostgresBaseRates(restricted)
            self.assertEqual(reader.latest(), self.observation)
            with self.assertRaises(BaseRateUnavailable):
                reader.save(self.observation)
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with psycopg.connect(restricted) as connection:
                    connection.execute("SELECT * FROM public.f13_policy_documents")
        finally:
            with psycopg.connect(self.url) as connection:
                connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            with psycopg.connect(self.base, autocommit=True) as connection:
                connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
