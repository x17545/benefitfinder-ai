"""Real PostgreSQL rate persistence in a disposable, fictional test database."""

from datetime import date, datetime, timezone
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from backend.tests.catalog_fixtures import MemoryPolicies, release_data
from benefitfinder.application.base_rates import BaseRateObservation, BaseRateUnavailable
from benefitfinder.application.collection import SourceFetcher, SourceResponse
from benefitfinder.bootstrap import create_app
from benefitfinder.infrastructure.database.repositories.base_rates import PostgresBaseRates
from benefitfinder.jobs.__main__ import main

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

    def test_daily_cli_isolates_real_file_and_postgres_failures_and_reports_once(self) -> None:
        self.repository.save(self.observation)
        newer = BaseRateObservation(
            0.03, date(2026, 11, 2), datetime(2026, 11, 3, tzinfo=timezone.utc)
        )
        body = {
            "resultCode": 200,
            "result": {
                "youthPolicyList": [{"plcyNo": "fictional-A", "plcyNm": "가상 공고"}],
                "pagging": {"totCount": 1},
            },
        }
        fetcher = Mock(spec=SourceFetcher)
        fetcher.fetch.return_value = SourceResponse(400, b"fictional failure", None)
        with (
            TemporaryDirectory() as temporary,
            patch.dict(
                "os.environ",
                {
                    "YOUTHCENTER_API_KEY": "fictional-key",
                    "ECOS_API_KEY": "fictionalkey",
                    "BASE_RATE_WRITE_DATABASE_URL": self.url,
                    "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/123/fictional-token",
                },
            ),
            patch("benefitfinder.jobs.__main__.YouthcenterHttpFetcher", return_value=fetcher),
            patch(
                "benefitfinder.jobs.__main__.EcosBaseRate",
            ) as ecos,
            patch("benefitfinder.jobs.__main__.DiscordWebhook.send") as notify,
            redirect_stdout(
                StringIO(),
            ),
            redirect_stderr(StringIO()),
        ):
            data = Path(temporary)
            command = ["refresh-data", "--data-dir", temporary, "--notify-discord"]
            ecos.return_value.fetch.return_value = newer
            self.assertEqual(main(command), 1)
            self.assertEqual(self.repository.latest(), newer)
            policy_status = data / "processed/f08-youthcenter/status.json"
            self.assertIsNone(json.loads(policy_status.read_text())["last_success"])
            fetcher.fetch.return_value = SourceResponse(
                200, json.dumps(body).encode(), "application/json"
            )
            ecos.return_value.fetch.side_effect = BaseRateUnavailable("PRIVATE KEY DSN")
            self.assertEqual(main(command), 1)
            selected = json.loads(policy_status.read_text())["last_success"]["run_id"]
            records = (
                data / "processed/f08-youthcenter/runs" / selected / "youthcenter/records.json"
            )
            self.assertIn("fictional-A", json.loads(records.read_text()))
            self.assertEqual(self.repository.latest(), newer)
            ecos.return_value.fetch.side_effect = None
            self.assertEqual(main(command), 0)
            summary = json.loads((data / "processed/f08/status.json").read_text())
            self.assertEqual(summary["last_attempt"]["notification_status"], "sent")
            self.assertIsNotNone(summary["last_success"])
            self.assertEqual(notify.call_count, 3)
            self.assertTrue(all("PRIVATE" not in call.args[0] for call in notify.call_args_list))

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
