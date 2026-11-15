"""Atomic approved catalogue/F13 publication in a dedicated fictional PostgreSQL DB."""

import asyncio
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi.testclient import TestClient
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

from backend.integration.postgres_fixture import test_database_url
from backend.integration.test_policy_source_rechecks_postgres import dedicated_database, batch, AT
from backend.tests.test_f12_catalog_files import review_fixture
from backend.tests.release_clause_fixtures import change_version, release_with_clauses
from backend.tests.test_published_catalog import validator
from benefitfinder.application.policy_explanations import ExplanationRequest, GeneratedExplanation
from benefitfinder.bootstrap import create_app
from benefitfinder.infrastructure.database.repositories.published_clauses import PostgresPublishedClauses
from benefitfinder.infrastructure.database.repositories.collected_policies import PostgresCollectedPolicies
from benefitfinder.infrastructure.database.repositories.published_policies import PostgresPublishedPolicies, publish_policy
from benefitfinder.infrastructure.database.repositories.published_services import PostgresPublishedServices, publish_service
from benefitfinder.infrastructure.policy_catalog_records import CatalogRecord, record_hash
from benefitfinder.infrastructure.service_catalog_records import ServiceCatalogRecord, service_record_hash
from benefitfinder.infrastructure.f12_catalog_files import prepare_f12_release

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "backend/migrations/versions"


class ReviewedReleasePostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database = dedicated_database(test_database_url())
        cls.url = cls.database.__enter__()
        with psycopg.connect(cls.url) as connection:
            for name in ("0007_f12_analysis_tasks.sql", "0008_service_catalog.sql",
                         "0009_gov24_policy_sources.sql", "0010_catalog_explanation_publication.sql"):
                connection.execute((MIGRATIONS / name).read_text())

    @classmethod
    def tearDownClass(cls) -> None:
        cls.database.__exit__(None, None, None)

    def setUp(self) -> None:
        with psycopg.connect(self.url) as connection:
            connection.execute("""TRUNCATE public.f12_analysis_sources,public.f12_analysis_tasks,public.policy_recheck_queue,public.policy_catalog_source_links,
                public.policy_catalog_releases,public.f13_clauses,public.f13_policy_documents""")

    def publish(self, data: dict | None = None, text: str | None = None, *, service: bool = False) -> None:
        if data is None:
            data, text = release_with_clauses(service=service)
        model = ServiceCatalogRecord if service else CatalogRecord
        record = model.model_validate(data)
        digest = service_record_hash(record) if service else record_hash(record)
        publish = publish_service if service else publish_policy
        kwargs = {"price_source_text": None} if service else {}
        publish(self.url, record, source_text=text, approved_sha256=digest,
                reviewer="fictional-full-release-reviewer", reviewed_at=datetime.now(timezone.utc), **kwargs)

    def search(self, data: dict) -> tuple:
        benefit = data["benefit"]
        request = ExplanationRequest(benefit["id"], benefit["title"], benefit["categories"][0],
                                     benefit["source"]["document_version"], 1_000_000, (("age", "met"),))
        return asyncio.run(PostgresPublishedClauses(self.url).search(request, limit=5))

    def test_exact_approval_publishes_cash_service_and_clauses_together(self) -> None:
        for service in (False, True):
            data, text = release_with_clauses(service=service)
            self.publish(data, text, service=service)
            repository = PostgresPublishedServices(self.url) if service else PostgresPublishedPolicies(self.url)
            self.assertIsNotNone(repository.get_published(data["benefit"]["id"]))
            self.assertEqual({c.text for c in self.search(data)},
                             {c["evidence"]["quote"] for c in data["explanation_clauses"]})
        with psycopg.connect(self.url) as connection:
            self.assertEqual(connection.execute("""SELECT count(*) FROM public.f13_clauses c
                JOIN public.policy_catalog_releases r USING (policy_id,document_version)
                WHERE c.reviewed_by=r.reviewed_by AND c.reviewed_at=r.reviewed_at
                  AND c.published_at=r.published_at""").fetchone()[0], 6)

    def test_clause_write_failure_rolls_back_catalogue_and_old_explanation_retirement(self) -> None:
        old, text = release_with_clauses()
        self.publish(old, text)
        newer = change_version(old, "fictional-v2")
        # Existing draft at the new key forces an actual DB insert failure.
        with psycopg.connect(self.url) as connection:
            connection.execute("""INSERT INTO public.f13_policy_documents
                (policy_id,document_version,policy_name,category,source_url,checked_at)
                VALUES (%s,'fictional-v2','가상 기존 초안','housing','https://example.test/',%s)""",
                (old['benefit']['id'], AT))
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.publish(newer, text)
        self.assertEqual(len(self.search(old)), 3)
        self.assertEqual(PostgresPublishedPolicies(self.url).get_published(old["benefit"]["id"]).detail["source"]["document_version"], "fictional-v1")
        self.assertEqual(self.search(newer), ())

    def test_version_rollover_and_removal_retire_previous_bound_clauses(self) -> None:
        old, text = release_with_clauses()
        self.publish(old, text)
        newer = change_version(old, "fictional-v2")
        self.publish(newer, text)
        self.assertEqual(self.search(old), ())
        self.assertEqual(len(self.search(newer)), 3)
        without = change_version(newer, "fictional-v3")
        without.pop("explanation_clauses")
        self.publish(without, text)
        self.assertEqual(self.search(newer), ())
        self.assertEqual(self.search(without), ())

    def test_direct_clause_and_document_edits_and_approval_detachment_are_rejected(self) -> None:
        data, text = release_with_clauses()
        self.publish(data, text)
        statements = (
            "UPDATE public.f13_clauses SET body='forged'",
            "UPDATE public.f13_clauses SET topics=ARRAY['special']",
            "UPDATE public.f13_clauses SET reviewed_by='other'",
            "UPDATE public.f13_policy_documents SET source_url='https://forged.example/'",
            "UPDATE public.f13_policy_documents SET catalog_record_sha256=NULL",
        )
        for statement in statements:
            with self.subTest(statement=statement), self.assertRaises(psycopg.errors.RaiseException):
                with psycopg.connect(self.url) as connection:
                    connection.execute(statement)
        self.assertEqual(len(self.search(data)), 3)

    def test_actual_api_searches_approved_same_version_clauses_for_ai_explanation(self) -> None:
        a, text = release_with_clauses("A")
        b, other_text = release_with_clauses("B")
        self.publish(a, text)
        self.publish(b, other_text)
        generator = AsyncMock()
        generator.generate.return_value = GeneratedExplanation("신청 방법을 확인해 주세요.", ("application",))
        request = json.loads((ROOT / "contracts/fixtures/recommendation-request.json").read_text())
        request["profile"]["age"] = 25
        request["profile"]["domain_data"]["interests"] = ["housing"]
        with TestClient(create_app(policy_database_url=self.url, explanation_database_url=self.url,
                                   explanation_generator=generator)) as client:
            response = client.post("/api/v1/recommendations", json=request)
            self.assertEqual(response.status_code, 200, response.text)
            validator("recommendation-response.schema.json").validate(response.json())
            self.assertTrue(response.json()["ai_generated"])
            policies = response.json()["results"]["1y"]["pv_off"]["portfolios"][0]["policies"]
            self.assertTrue(all(p["ai_sources"][0]["location"] == "[가상] 신청 안내" for p in policies))
            self.assertEqual(generator.generate.await_count, 2)

    def test_original_f12_decisions_compile_publish_and_search_without_automatic_approval(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _, _, _, facts, _ = review_fixture(root)
            record = prepare_f12_release(pair_dir=root / 'pair', decisions_path=root / 'decisions.json',
                approved_path=root / 'approved.json', record_path=root / 'record.json',
                plan_path=root / 'plan.json', source_text=facts.document.text)
            with self.assertRaisesRegex(ValueError, 'invalid_catalog_approval'):
                publish_policy(self.url, record, source_text=facts.document.text, approved_sha256='0' * 64,
                               reviewer='fictional-human', reviewed_at=datetime.now(timezone.utc))
            with self.assertRaisesRegex(ValueError, 'approval_predates_f12_review'):
                publish_policy(self.url, record, source_text=facts.document.text, approved_sha256=record_hash(record),
                               reviewer='fictional-human', reviewed_at=facts.document.collected_at)
            self.publish(record.model_dump(mode='json'), facts.document.text)
            self.assertEqual(self.search(record.model_dump(mode='json'))[0].text, facts.eligibility[0].quote)

    def test_catalogue_only_publisher_permissions_create_all_clauses_without_f13_write_grants(self):
        role = 'bf_release_' + uuid4().hex
        with psycopg.connect(self.url) as connection:
            connection.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'fictional-review-test-only'").format(sql.Identifier(role)))
            connection.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(role)))
            connection.execute(sql.SQL('GRANT SELECT,INSERT ON public.policy_catalog_releases TO {}').format(sql.Identifier(role)))
            connection.execute(sql.SQL('GRANT UPDATE (publication_status) ON public.policy_catalog_releases TO {}').format(sql.Identifier(role)))
            connection.execute(sql.SQL('GRANT SELECT (version) ON public.benefitfinder_schema_migrations TO {}').format(sql.Identifier(role)))
        role_url = make_conninfo(self.url, user=role, password='fictional-review-test-only')
        try:
            for service in (False, True):
                data, text = release_with_clauses(service=service)
                record = (ServiceCatalogRecord if service else CatalogRecord).model_validate(data)
                digest = service_record_hash(record) if service else record_hash(record)
                kwargs = {'price_source_text': None} if service else {}
                (publish_service if service else publish_policy)(role_url, record, source_text=text,
                    approved_sha256=digest, reviewer='fictional-human', reviewed_at=datetime.now(timezone.utc), **kwargs)
                self.assertEqual(len(self.search(data)), 3)
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with psycopg.connect(role_url) as connection:
                    connection.execute("DELETE FROM public.f13_clauses")
        finally:
            with psycopg.connect(self.url) as connection:
                connection.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
                connection.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))

    def test_f13_rechecks_catalogue_digest_even_for_direct_sql_insert(self):
        data, text = release_with_clauses()
        record = CatalogRecord.model_validate(data)
        now = datetime.now(timezone.utc)
        with psycopg.connect(self.url) as connection:
            connection.execute("""INSERT INTO public.policy_catalog_releases
                (policy_id,document_version,payload,approved_sha256,review_status,reviewed_by,
                 reviewed_at,publication_status,published_at) VALUES (%s,%s,%s,%s,'approved',%s,%s,'published',%s)""",
                (record.benefit.id, record.benefit.source.document_version, Jsonb(record.model_dump(mode='json')),
                 '0' * 64, 'fictional-human', now, now))
        with self.assertRaisesRegex(RuntimeError, 'published_clause_lookup_failed'):
            self.search(data)

    def test_bound_dual_category_clauses_are_found_for_each_supported_category(self):
        data, text = release_with_clauses()
        data['benefit']['categories'] = ['employment', 'housing']
        self.publish(data, text)
        data['benefit']['categories'] = ['housing', 'employment']
        self.assertEqual(len(self.search(data)), 3)

    def test_source_change_retires_catalogue_and_bound_clauses_without_reapproval_on_reappearance(self):
        data, text = release_with_clauses()
        store = PostgresCollectedPolicies(self.url)
        store.save(batch({'reviewed-A': 'original'}, AT))
        data['benefit']['virtual_data'] = False
        data['collection_sources'] = [{'source': 'youthcenter', 'external_id': 'reviewed-A',
                                      'material_sha256': hashlib.sha256(b'original').hexdigest()}]
        self.publish(data, text)
        self.assertEqual(len(self.search(data)), 3)
        store.save(batch({'reviewed-A': 'changed'}, AT + timedelta(hours=1)))
        self.assertEqual(self.search(data), ())
        self.assertIsNone(PostgresPublishedPolicies(self.url).get_published(data['benefit']['id']))
        store.save(batch({'reviewed-A': 'original'}, AT + timedelta(hours=2)))
        self.assertEqual(self.search(data), ())

    def test_same_version_republication_is_rejected_without_retiring_current_rows(self):
        data, text = release_with_clauses()
        self.publish(data, text)
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.publish(data, text)
        self.assertEqual(len(self.search(data)), 3)
