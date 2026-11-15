"""Real local files -> collection DB -> daily report, with synthetic HTTP inputs."""

from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime, timezone
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

from benefitfinder.application.base_rates import BaseRateObservation
from benefitfinder.application.collected_policies import CollectedPolicyStoreError
from benefitfinder.application.collection import SourceFetcher, SourceResponse
from benefitfinder.bootstrap import create_app
from benefitfinder.jobs.__main__ import main

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations/versions"


class CollectedWorkflowPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base = os.environ.get("F13_TEST_DATABASE_URL", "")
        if not cls.base or not conninfo_to_dict(cls.base).get("dbname", "").endswith("_test"):
            raise ValueError("dedicated_test_database_required")
        cls.name = "bf_collection_flow_" + uuid4().hex[:12] + "_test"
        with psycopg.connect(cls.base, autocommit=True) as connection:
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(cls.name)))
        cls.url = make_conninfo(cls.base, dbname=cls.name)
        try:
            with psycopg.connect(cls.url) as connection:
                for name in (
                    "0001_f13_published_clauses.sql",
                    "0002_published_policy_catalog.sql",
                    "0004_ecos_base_rate.sql",
                    "0005_collected_policy_generations.sql",
                ):
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
            connection.execute(
                "TRUNCATE public.collected_policy_changes, public.collected_policies, "
                "public.collected_policy_runs, public.ecos_base_rate"
            )

    def count(self, table: str) -> int:
        with psycopg.connect(self.url) as connection:
            return connection.execute(
                sql.SQL("SELECT count(*) FROM public.{}").format(sql.Identifier(table))
            ).fetchone()[0]

    @staticmethod
    def response(title: str, *, include_second: bool = False) -> SourceResponse:
        rows = [{"plcyNo": "fictional-A", "plcyNm": title}]
        if include_second:
            rows.append({"plcyNo": "fictional-B", "plcyNm": "가상 주거 공고"})
        body = {
            "resultCode": 200,
            "result": {
                "youthPolicyList": rows,
                "pagging": {"totCount": len(rows)},
            },
        }
        return SourceResponse(200, json.dumps(body).encode(), "application/json")

    def test_daily_failure_recovery_replay_and_db_based_changes(self) -> None:
        fetcher = Mock(spec=SourceFetcher)
        fetcher.fetch.return_value = self.response("가상 최초 공고")
        output = StringIO()
        rate = BaseRateObservation(
            0.03, date(2026, 11, 1), datetime(2026, 11, 2, tzinfo=timezone.utc)
        )
        with (
            TemporaryDirectory() as temporary,
            patch.dict(
                "os.environ",
                {
                    "YOUTHCENTER_API_KEY": "fictional-key",
                    "ECOS_API_KEY": "fictionalkey",
                    "BASE_RATE_WRITE_DATABASE_URL": self.url,
                    "COLLECTION_WRITE_DATABASE_URL": self.url,
                    "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/123/fictional-token",
                },
                clear=True,
            ),
            patch("benefitfinder.jobs.__main__.YouthcenterHttpFetcher", return_value=fetcher),
            patch("benefitfinder.jobs.__main__.EcosBaseRate") as ecos,
            patch("benefitfinder.jobs.__main__.DiscordWebhook.send") as notify,
            redirect_stdout(output),
            redirect_stderr(output),
        ):
            ecos.return_value.fetch.return_value = rate
            daily = ["refresh-data", "--data-dir", temporary, "--notify-discord"]
            status_path = Path(temporary) / "processed/f08-youthcenter/status.json"
            with patch(
                "benefitfinder.jobs.__main__.PostgresCollectedPolicies.save",
                side_effect=CollectedPolicyStoreError("PRIVATE DSN source text"),
            ):
                self.assertEqual(main(daily), 1)
                selected = json.loads(status_path.read_text())["last_success"]["run_id"]
                self.assertEqual(self.count("collected_policy_runs"), 0)
                self.assertEqual(self.count("ecos_base_rate"), 1)
                self.assertEqual(main(daily), 1)
                self.assertEqual(fetcher.fetch.call_count, 1)
                self.assertEqual(
                    json.loads(status_path.read_text())["last_success"]["run_id"], selected
                )
            # Restore real DB writes; only its connection is needed for recovery.
            with patch.dict("os.environ", {"COLLECTION_WRITE_DATABASE_URL": self.url}, clear=True):
                replay = ["ingest-youthcenter", "--data-dir", temporary]
                self.assertEqual(main(replay), 0)
                self.assertEqual(main(replay), 0)
            self.assertEqual(fetcher.fetch.call_count, 1)
            self.assertEqual(self.count("collected_policy_runs"), 1)
            self.assertEqual(self.count("collected_policy_changes"), 1)
            fetcher.fetch.return_value = self.response("가상 변경 공고", include_second=True)
            self.assertEqual(main(daily), 0)
            self.assertEqual(self.count("collected_policies"), 2)
            self.assertEqual(self.count("collected_policy_runs"), 2)
            self.assertEqual(self.count("collected_policy_changes"), 3)
            fetcher.fetch.return_value = SourceResponse(400, b"fictional failure", None)
            self.assertEqual(main(daily), 1)
            self.assertEqual(self.count("collected_policy_runs"), 2)
            self.assertEqual(self.count("collected_policy_changes"), 3)
            self.assertEqual(notify.call_count, 4)
            for call in notify.call_args_list:
                self.assertIn("ECOS 기준금리: 성공", call.args[0])
                self.assertNotIn("PRIVATE", call.args[0])
            self.assertNotIn("PRIVATE", output.getvalue())

    def test_unreviewed_source_never_becomes_a_published_api_policy(self) -> None:
        fetcher = Mock(spec=SourceFetcher)
        fetcher.fetch.return_value = self.response("가상 미검수 취업 공고")
        with (
            TemporaryDirectory() as temporary,
            patch.dict(
                "os.environ",
                {
                    "YOUTHCENTER_API_KEY": "fictional-key",
                    "COLLECTION_WRITE_DATABASE_URL": self.url,
                },
                clear=True,
            ),
            patch("benefitfinder.jobs.__main__.YouthcenterHttpFetcher", return_value=fetcher),
            redirect_stdout(StringIO()),
            redirect_stderr(StringIO()),
        ):
            self.assertEqual(main(["refresh-youthcenter", "--data-dir", temporary]), 0)
        self.assertEqual(self.count("collected_policies"), 1)
        self.assertEqual(self.count("policy_catalog_releases"), 0)
        self.assertEqual(self.count("f13_policy_documents"), 0)
        with TestClient(create_app(policy_database_url=self.url)) as client:
            self.assertEqual(client.get("/api/v1/benefits/youth:fictional-A").status_code, 404)
            self.assertEqual(client.get("/api/v1/meta").status_code, 503)
