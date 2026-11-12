"""Source changes revoke live catalog/clauses atomically in real PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextlib import redirect_stdout, redirect_stderr
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from io import StringIO
from pathlib import Path
import unittest
import tempfile
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb
from fastapi.testclient import TestClient

from backend.tests.catalog_fixtures import release_data, source_text
from benefitfinder.application.collected_policies import CollectedPolicyBatch, CollectedPolicyStoreError
from benefitfinder.application.collection import CollectedRecord
from benefitfinder.application.policy_explanations import ExplanationRequest
from benefitfinder.bootstrap import create_app
from benefitfinder.infrastructure.database.repositories.collected_policies import PostgresCollectedPolicies
from benefitfinder.infrastructure.database.repositories.policy_rechecks import list_policy_rechecks
from benefitfinder.infrastructure.database.repositories.published_policies import (
    PostgresPublishedPolicies, publish_policy,
)
from benefitfinder.infrastructure.database.repositories.published_clauses import PostgresPublishedClauses
from benefitfinder.infrastructure.policy_catalog_records import CatalogRecord, record_hash
from benefitfinder.jobs.__main__ import main

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations/versions"
NAMES = ("0001_f13_published_clauses.sql", "0002_published_policy_catalog.sql",
         "0005_collected_policy_generations.sql", "0006_policy_source_rechecks.sql")
AT = datetime(2026, 11, 2, 1, tzinfo=timezone.utc)


@contextmanager
def dedicated_database(base: str, *, legacy: bool = False):
    name = "bf_rechecks_" + uuid4().hex + "_test"
    with psycopg.connect(base, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = make_conninfo(base, dbname=name)
    try:
        with psycopg.connect(url) as connection:
            for item in NAMES[:-1] if legacy else NAMES:
                connection.execute((MIGRATIONS / item).read_text())
        yield url
    finally:
        with psycopg.connect(base, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def batch(materials: dict[str, str], at: datetime) -> CollectedPolicyBatch:
    return CollectedPolicyBatch("youthcenter", uuid4().hex, at, tuple(
        CollectedRecord("youthcenter", key, hashlib.sha256(value.encode()).hexdigest(),
                        "fictional-snapshot/" + key, at - timedelta(seconds=1),
                        {"plcyNo": key, "fictional_material": value})
        for key, value in materials.items()
    ))


def record(key: str, sources: dict[str, str], version: str = "fictional-v1") -> CatalogRecord:
    data = release_data(key)
    data["benefit"]["source"]["document_version"] = version
    for evidence in data["payment_evidence"] + [e for c in data["benefit"]["eligibility_conditions"]
                                               for e in c["evidence"]]:
        evidence["source"]["document_version"] = version
    data["collection_sources"] = [{"source": "youthcenter", "external_id": key,
                                   "material_sha256": hashlib.sha256(value.encode()).hexdigest()}
                                  for key, value in sources.items()]
    return CatalogRecord.model_validate(data)


def publish(url: str, item: CatalogRecord, reviewed_at: datetime = AT) -> None:
    publish_policy(url, item, source_text=source_text(), approved_sha256=record_hash(item),
                   reviewer="fictional-human-reviewer", reviewed_at=reviewed_at)


def clauses(url: str, policy_id: str) -> None:
    with psycopg.connect(url) as connection:
        for version in ("fictional-v1", "fictional-supplement"):
            connection.execute("""INSERT INTO public.f13_policy_documents
                (policy_id,document_version,policy_name,category,source_url,checked_at,
                 publication_status,published_at) VALUES (%s,%s,'가상 정책','housing',
                 'https://example.test/fictional',%s,%s,%s)""",
                (policy_id, version, AT, "published" if version == "fictional-v1" else "draft", AT))
            connection.execute("""INSERT INTO public.f13_clauses
                (policy_id,document_version,clause_id,title,body,source_location,topics,
                 review_status,reviewed_by,reviewed_at,publication_status,published_at)
                VALUES (%s,%s,'fictional-clause','가상 조건','가상 근거입니다.','가상 안내',
                        ARRAY['housing'],'approved','fictional-human-reviewer',%s,'published',%s)""",
                (policy_id, version, AT, AT))


class PolicySourceRechecksPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base = os.environ.get("F13_TEST_DATABASE_URL", "")
        if not cls.base or not conninfo_to_dict(cls.base).get("dbname", "").endswith("_test"):
            raise ValueError("dedicated_test_database_required")
        cls.database = dedicated_database(cls.base)
        cls.url = cls.database.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.database.__exit__(None, None, None)

    def setUp(self) -> None:
        with psycopg.connect(self.url) as connection:
            connection.execute("""TRUNCATE public.policy_recheck_queue,
                public.policy_catalog_source_links,public.policy_catalog_releases,
                public.collected_policy_changes,public.collected_policies,public.collected_policy_runs,
                public.f13_clauses,public.f13_policy_documents RESTART IDENTITY""")
        self.store = PostgresCollectedPolicies(self.url)
        self.catalog = PostgresPublishedPolicies(self.url)

    def seed(self) -> CollectedPolicyBatch:
        first = batch({"raw-a": "a-v1", "raw-b": "b-v1", "raw-c": "c-v1"}, AT)
        self.store.save(first)
        publish(self.url, record("A", {"raw-a": "a-v1"}))
        publish(self.url, record("B", {"raw-a": "a-v1", "raw-b": "b-v1"}))
        publish(self.url, record("C", {"raw-c": "c-v1"}))
        clauses(self.url, "youth:catalog:A")
        return first

    def test_change_revokes_only_explicit_dependents_and_http_calculations(self) -> None:
        self.seed()
        before = record_hash(record("A", {"raw-a": "a-v1"}))
        request = ExplanationRequest("youth:catalog:A", "가상 정책", "housing", "fictional-v1", None,
                                     (("housing", "met"),))
        async def search() -> tuple:
            return await PostgresPublishedClauses(self.url).search(request, limit=5)
        self.assertEqual(len(asyncio.run(search())), 1)
        changed = batch({"raw-a": "a-v2", "raw-b": "b-v1", "raw-c": "c-v1"}, AT + timedelta(days=1))
        self.store.save(changed)
        self.assertEqual([p.policy_id for p in self.catalog.list_published()], ["youth:catalog:C"])
        self.assertEqual(asyncio.run(search()), ())
        with psycopg.connect(self.url) as connection:
            row = connection.execute("""SELECT review_status,publication_status,approved_sha256,
                reviewed_by,reviewed_at FROM public.policy_catalog_releases WHERE policy_id='youth:catalog:A'""").fetchone()
            self.assertEqual(row, ("pending", "retired", before, "fictional-human-reviewer", AT))
            self.assertEqual(connection.execute("SELECT DISTINCT publication_status FROM public.f13_policy_documents").fetchall(), [("retired",)])
            self.assertEqual(connection.execute("SELECT DISTINCT review_status,publication_status FROM public.f13_clauses").fetchall(), [("pending", "retired")])
        page = list_policy_rechecks(self.url, limit=1)
        self.assertEqual(len(page["items"]), 1)
        self.assertIsNotNone(page["next_after_id"])
        second = list_policy_rechecks(self.url, after_id=page["next_after_id"], limit=1)
        self.assertIsNone(second["next_after_id"])
        self.assertEqual({item["policy_id"] for item in page["items"] + second["items"]}, {"youth:catalog:A", "youth:catalog:B"})
        item = page["items"][0]
        self.assertEqual(item["reason"], "source_changed")
        self.assertEqual(item["latest_run_id"], changed.run_id)
        self.assertEqual(item["current_material_sha256"], item["latest_material_sha256"])
        self.assertNotIn("fictional_material", json.dumps(page))
        root = Path(__file__).resolve().parents[2]
        payload = json.loads((root / "contracts/fixtures/recommendation-request.json").read_text())
        payload["profile"]["age"] = 25
        payload["profile"]["domain_data"]["interests"] = ["housing"]
        with TestClient(create_app(policy_database_url=self.url)) as client:
            self.assertEqual(client.get("/api/v1/benefits/youth:catalog:A").status_code, 404)
            response = client.post("/api/v1/recommendations", json=payload)
            self.assertEqual(response.status_code, 200)
            result = response.json()["results"]["1y"]["pv_off"]
            self.assertEqual(response.json()["view"], "fallback_list")
            self.assertEqual([(item["policy_id"], item["amount_in_period_won"])
                              for item in result["fallback_list"]], [("youth:catalog:C", 1_000_000)])
            self.assertNotIn("youth:catalog:A", json.dumps(result))

    def test_unchanged_and_replay_do_not_invalidate_or_duplicate(self) -> None:
        first = self.seed()
        self.assertTrue(self.store.save(first).replayed)
        self.store.save(batch({"raw-a": "a-v1", "raw-b": "b-v1", "raw-c": "c-v1"}, AT + timedelta(days=1)))
        self.assertEqual(len(self.catalog.list_published()), 3)
        self.assertEqual(list_policy_rechecks(self.url)["items"], [])

    def test_missing_reappearance_and_reapproval_need_a_new_version(self) -> None:
        first = batch({"raw-a": "a-v1"}, AT)
        self.store.save(first)
        publish(self.url, record("A", {"raw-a": "a-v1"}))
        clauses(self.url, "youth:catalog:A")
        missing = batch({}, AT + timedelta(days=1))
        self.store.save(missing)
        self.assertIsNone(self.catalog.get_published("youth:catalog:A"))
        item = list_policy_rechecks(self.url)["items"][0]
        self.assertEqual(item["reason"], "source_missing")
        self.assertIsNone(item["current_material_sha256"])
        self.assertIsNotNone(item["latest_missing_detected_at"])
        with self.assertRaises(psycopg.Error):
            publish(self.url, record("A", {"raw-a": "a-v1"}, "fictional-v2"))
        again = batch({"raw-a": "a-v1"}, AT + timedelta(days=2))
        self.store.save(again)
        self.assertIsNone(self.catalog.get_published("youth:catalog:A"))
        with psycopg.connect(self.url) as connection:
            for table in ("policy_catalog_releases", "f13_policy_documents", "f13_clauses"):
                with self.subTest(table=table), self.assertRaises(psycopg.Error), connection.transaction():
                    connection.execute(sql.SQL("UPDATE public.{} SET publication_status='published' WHERE policy_id='youth:catalog:A'").format(sql.Identifier(table)))
        with self.assertRaisesRegex(psycopg.Error, "approval_predates_source_change"):
            publish(self.url, record("A", {"raw-a": "a-v1"}, "fictional-v2"))
        publish(self.url, record("A", {"raw-a": "a-v1"}, "fictional-v2"), AT + timedelta(days=3))
        self.assertIsNotNone(self.catalog.get_published("youth:catalog:A"))
        self.assertEqual(len(list_policy_rechecks(self.url)["items"]), 1)
        item = list_policy_rechecks(self.url)["items"][0]
        self.assertEqual(item["latest_run_id"], again.run_id)
        self.assertIsNone(item["latest_missing_detected_at"])
        self.assertEqual(item["recheck_status"], "superseded")
        self.assertEqual(item["superseded_by_version"], "fictional-v2")

    def test_stale_or_unknown_binding_rolls_back_current_publication(self) -> None:
        self.seed()
        for links in ({"raw-a": "wrong-hash"}, {"unknown-id": "a-v1"}):
            with self.subTest(links=links), self.assertRaises(psycopg.Error):
                publish(self.url, record("A", links, "fictional-v2"))
            self.assertIsNotNone(self.catalog.get_published("youth:catalog:A"))
        with psycopg.connect(self.url) as connection:
            count = connection.execute("SELECT count(*) FROM public.policy_catalog_source_links WHERE policy_id='youth:catalog:A'").fetchone()[0]
            self.assertEqual(count, 1)

    def test_invalidation_failure_rolls_back_source_and_approval_together(self) -> None:
        first = self.seed()
        with psycopg.connect(self.url) as connection:
            connection.execute("""CREATE FUNCTION public.fail_recheck_test() RETURNS trigger
                LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'fictional-sensitive-failure'; END $$;
                CREATE TRIGGER fail_recheck_test BEFORE INSERT ON public.policy_recheck_queue
                FOR EACH ROW EXECUTE FUNCTION public.fail_recheck_test()""")
        try:
            with self.assertRaisesRegex(CollectedPolicyStoreError, "^collected_policy_storage_unavailable$"):
                self.store.save(batch({"raw-a": "a-v2"}, AT + timedelta(days=1)))
            self.assertEqual(len(self.catalog.list_published()), 3)
            with psycopg.connect(self.url) as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM public.collected_policy_runs").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT last_run_id FROM public.collected_policies WHERE external_id='raw-a'").fetchone()[0], first.run_id)
                self.assertEqual(connection.execute("SELECT count(*) FROM public.policy_recheck_queue").fetchone()[0], 0)
        finally:
            with psycopg.connect(self.url) as connection:
                connection.execute("DROP TRIGGER fail_recheck_test ON public.policy_recheck_queue; DROP FUNCTION public.fail_recheck_test()")

    def test_source_lock_is_taken_before_retiring_previous_release(self) -> None:
        self.seed()
        with psycopg.connect(self.url) as connection:
            connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("benefitfinder:collected_policy_generations:youthcenter",))
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(publish, self.url, record("A", {"raw-a": "a-v1"}, "fictional-v2"))
                with self.assertRaises(psycopg.errors.LockNotAvailable):
                    future.result(timeout=3)
            self.assertEqual(connection.execute("SELECT publication_status FROM public.policy_catalog_releases WHERE policy_id='youth:catalog:A'").fetchone()[0], "published")
        self.assertIsNotNone(self.catalog.get_published("youth:catalog:A"))

    def test_migration_quarantines_unlinked_real_releases_but_preserves_fictional(self) -> None:
        with dedicated_database(self.base, legacy=True) as url:
            publish(url, CatalogRecord.model_validate(release_data("VIRTUAL")))
            with self.assertRaisesRegex(ValueError, "catalog_source_migration_required"):
                publish(url, record("NO-MIGRATION", {"raw-a": "a-v1"}))
            data = release_data("LEGACY")
            digest = record_hash(CatalogRecord.model_validate(data))
            data["benefit"]["virtual_data"] = False
            with psycopg.connect(url) as connection:
                connection.execute("""INSERT INTO public.policy_catalog_releases
                    (policy_id,document_version,payload,approved_sha256,review_status,
                     reviewed_by,reviewed_at,publication_status,published_at)
                    VALUES ('youth:catalog:LEGACY','fictional-v1',%s,%s,'approved',
                            'fictional-human-reviewer',%s,'published',%s)""", (Jsonb(data), digest, AT, AT))
            clauses(url, "youth:catalog:LEGACY")
            with psycopg.connect(url) as connection:
                connection.execute((MIGRATIONS / NAMES[-1]).read_text())
                self.assertEqual(connection.execute("SELECT review_status,publication_status FROM public.policy_catalog_releases WHERE policy_id='youth:catalog:LEGACY'").fetchone(), ("pending", "retired"))
                self.assertEqual(connection.execute("SELECT DISTINCT review_status,publication_status FROM public.f13_clauses").fetchall(), [("pending", "retired")])
            self.assertEqual([p.policy_id for p in PostgresPublishedPolicies(url).list_published()], ["youth:catalog:VIRTUAL"])
            item = list_policy_rechecks(url)["items"][0]
            self.assertEqual(item["reason"], "source_unlinked")
            self.assertIsNone(item["latest_material_sha256"])

    def test_narrow_collection_role_can_invalidate_without_catalog_or_queue_access(self) -> None:
        self.seed()
        role = "bf_source_" + uuid4().hex[:12]
        name = conninfo_to_dict(self.url)["dbname"]
        try:
            with psycopg.connect(self.url) as connection:
                connection.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'fictional-test-only'").format(sql.Identifier(role)))
                connection.execute(sql.SQL("GRANT CONNECT,TEMPORARY ON DATABASE {} TO {}").format(sql.Identifier(name), sql.Identifier(role)))
                connection.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
                connection.execute(sql.SQL("GRANT SELECT,INSERT ON public.collected_policy_runs,public.collected_policy_changes TO {}").format(sql.Identifier(role)))
                connection.execute(sql.SQL("GRANT SELECT,INSERT,UPDATE ON public.collected_policies TO {}").format(sql.Identifier(role)))
            restricted = make_conninfo(self.url, user=role, password="fictional-test-only")
            PostgresCollectedPolicies(restricted).save(batch({"raw-a": "a-v2", "raw-b": "b-v1", "raw-c": "c-v1"}, AT + timedelta(days=1)))
            self.assertEqual(len(self.catalog.list_published()), 1)
            for table in ("policy_catalog_releases", "policy_recheck_queue"):
                with self.subTest(table=table), psycopg.connect(restricted) as connection:
                    with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                        connection.execute(sql.SQL("SELECT * FROM public.{}").format(sql.Identifier(table)))
            with psycopg.connect(self.url) as connection:
                self.assertFalse(connection.execute("SELECT has_function_privilege(%s,'public.invalidate_changed_policy_sources()','EXECUTE')", (role,)).fetchone()[0])
        finally:
            with psycopg.connect(self.url) as connection:
                connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
                connection.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))

    def test_recheck_role_exports_with_column_grants_without_reading_raw_or_writing(self) -> None:
        self.seed()
        self.store.save(batch({"raw-a": "a-v2", "raw-b": "b-v1", "raw-c": "c-v1"}, AT + timedelta(days=1)))
        role = "bf_recheck_" + uuid4().hex[:12]
        name = conninfo_to_dict(self.url)["dbname"]
        try:
            with psycopg.connect(self.url) as connection:
                connection.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'fictional-test-only'").format(sql.Identifier(role)))
                connection.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(name), sql.Identifier(role)))
                connection.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
                connection.execute(sql.SQL("GRANT SELECT ON public.policy_recheck_queue TO {}").format(sql.Identifier(role)))
                connection.execute(sql.SQL("GRANT SELECT (source,external_id,material_sha256,missing_detected_at,last_run_id) ON public.collected_policies TO {}").format(sql.Identifier(role)))
                connection.execute(sql.SQL("GRANT SELECT (policy_id,document_version,published_at) ON public.policy_catalog_releases TO {}").format(sql.Identifier(role)))
            restricted = make_conninfo(self.url, user=role, password="fictional-test-only")
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "recheck-page.json"
                stdout, stderr = StringIO(), StringIO()
                with patch.dict(os.environ, {"POLICY_RECHECK_DATABASE_URL": restricted}), redirect_stdout(stdout), redirect_stderr(stderr):
                    self.assertEqual(main(["policy-catalog", "rechecks", "--output", str(output)]), 0)
                self.assertEqual(json.loads(stdout.getvalue())["exported_count"], 2)
                self.assertEqual(len(json.loads(output.read_text())["items"]), 2)
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)
                self.assertNotIn("fictional-human-reviewer", stdout.getvalue())
                self.assertNotIn("fictional-test-only", stdout.getvalue() + stderr.getvalue())
            for statement in ("SELECT fields FROM public.collected_policies",
                              "DELETE FROM public.policy_recheck_queue"):
                with self.subTest(statement=statement), self.assertRaises(psycopg.errors.InsufficientPrivilege), psycopg.connect(restricted) as connection:
                    connection.execute(statement)
        finally:
            with psycopg.connect(self.url) as connection:
                connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
                connection.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))

    def test_direct_publication_cannot_bypass_binding_validation(self) -> None:
        self.seed()
        valid = record("RAW", {"raw-a": "a-v1"})
        link = valid.collection_sources[0].model_dump()
        for sources in (None, [{}], [link, link], [{**link, "material_sha256": None}],
                        [{**link, "extra": "rejected"}], []):
            data = valid.model_dump(mode="json")
            data["benefit"]["virtual_data"] = False
            data["collection_sources"] = sources
            with self.subTest(sources=sources), self.assertRaises(psycopg.Error), psycopg.connect(self.url) as connection:
                connection.execute("""INSERT INTO public.policy_catalog_releases
                    (policy_id,document_version,payload,approved_sha256,review_status,
                     reviewed_by,reviewed_at,publication_status,published_at)
                    VALUES ('youth:catalog:RAW','fictional-v1',%s,%s,'approved',
                            'fictional-human-reviewer',%s,'published',%s)""",
                    (Jsonb(data), record_hash(valid), AT, datetime.now(timezone.utc)))
        self.assertEqual(len(self.catalog.list_published()), 3)
