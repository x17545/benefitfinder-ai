"""AI and human publication checks on an isolated fictional PostgreSQL database."""

import unittest
import json
from tempfile import TemporaryDirectory
from unittest.mock import patch
from datetime import timedelta
from pathlib import Path

import psycopg
from fastapi.testclient import TestClient

from backend.integration.postgres_fixture import test_database_url
from backend.integration.test_policy_source_rechecks_postgres import AT, batch, dedicated_database, record
from backend.tests.catalog_fixtures import source_text
from backend.tests.service_catalog_fixtures import service_release_data, service_source_text, price_source_text
from benefitfinder.infrastructure.ai_catalog_review import AIReview, publish_ai_batch, retire_ai_batch
from benefitfinder.infrastructure.database.repositories.collected_policies import PostgresCollectedPolicies
from benefitfinder.infrastructure.database.repositories.published_policies import PostgresPublishedPolicies, publish_policy
from benefitfinder.infrastructure.policy_catalog_records import record_hash
from benefitfinder.infrastructure.service_catalog_records import ServiceCatalogRecord, service_record_hash
from benefitfinder.infrastructure.database.repositories.published_services import PostgresPublishedServices
from benefitfinder.jobs.__main__ import main
from benefitfinder.bootstrap import create_app

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations/versions"


class AICatalogPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database = dedicated_database(test_database_url())
        cls.url = cls.database.__enter__()
        with psycopg.connect(cls.url) as connection:
            for path in sorted(MIGRATIONS.glob("*.sql")):
                if "0007" <= path.name[:4] <= "0013":
                    connection.execute(path.read_text())

    @classmethod
    def tearDownClass(cls):
        cls.database.__exit__(None, None, None)

    def setUp(self):
        with psycopg.connect(self.url) as connection:
            connection.execute("TRUNCATE public.collected_policy_runs, public.collected_policies, "
                               "public.policy_catalog_releases, public.f13_clauses, public.f13_policy_documents CASCADE")
        self.store = PostgresCollectedPolicies(self.url)
        self.store.save(batch({"src-A": "original-A", "src-B": "original-B"}, AT))
        self.a = record("A", {"src-A": "original-A"})
        self.b = record("B", {"src-B": "original-B"})
        self.reader = PostgresPublishedPolicies(self.url)

    def review(self, item, **kwargs):
        values = {"writer_agent": "fictional-writer", "reviewer_agent": "fictional-reviewer",
                  "reviewer_model": "fictional-model", "verdict": "pass", "reviewed_at": AT,
                  "document_version": item.benefit.source.document_version,
                  "source_sha256": item.source_sha256, "record_sha256": record_hash(item)}
        return AIReview.model_validate(values | kwargs)

    def entry(self, item, **kwargs):
        return (item, self.review(item, **kwargs), source_text(), None)

    def test_ai_pass_source_binding_and_existing_human_path(self):
        publish_ai_batch(self.url, [self.entry(self.a)])
        publish_policy(self.url, self.b, source_text=source_text(), approved_sha256=record_hash(self.b),
                       reviewer="fictional-human", reviewed_at=AT)
        self.assertEqual(len(self.reader.list_published()), 2)
        with psycopg.connect(self.url) as connection:
            rows = connection.execute("SELECT review_type,reviewed_by FROM public.policy_catalog_releases ORDER BY policy_id").fetchall()
        self.assertEqual(rows, [("ai", "ai:fictional-reviewer"), ("human", "fictional-human")])
        self.store.save(batch({"src-A": "changed-A", "src-B": "original-B"}, AT + timedelta(days=1)))
        self.assertIsNone(self.reader.get_published(self.a.benefit.id))
        self.assertIsNotNone(self.reader.get_published(self.b.benefit.id))
        with self.assertRaises(psycopg.errors.RaiseException):
            publish_ai_batch(self.url, [self.entry(self.a)])

    def test_fictional_ai_release_appears_in_real_recommendation_route(self):
        publish_ai_batch(self.url, [self.entry(self.a)])
        fixture = Path(__file__).resolve().parents[2] / "contracts/fixtures/recommendation-request.json"
        request = json.loads(fixture.read_text(encoding="utf-8"))
        request["profile"]["age"] = 25
        request["profile"]["domain_data"]["interests"] = ["housing"]
        with TestClient(create_app(policy_database_url=self.url)) as client:
            response = client.post("/api/v1/recommendations", json=request)
            self.assertEqual(response.status_code, 200, response.text)
            policies = response.json()["results"]["1y"]["pv_off"]["fallback_list"]
            self.assertIn(self.a.benefit.id, [item["policy_id"] for item in policies])
            self.assertEqual(policies[0]["amount_in_period_won"], 1_000_000)
            detail = client.get("/api/v1/benefits/" + self.a.benefit.id)
            self.assertEqual(detail.status_code, 200)
            self.assertEqual(detail.json()["source"]["url"], self.a.benefit.source.url)

    def test_service_source_and_price_are_verified(self):
        service = ServiceCatalogRecord.model_validate(service_release_data())
        review = self.review(self.a, document_version=service.benefit.source.document_version,
                             source_sha256=service.source_sha256, record_sha256=service_record_hash(service))
        with self.assertRaises(ValueError):
            publish_ai_batch(self.url, [(service, review, service_source_text(), price_source_text() + " changed")])
        publish_ai_batch(self.url, [(service, review, service_source_text(), price_source_text())])
        self.assertIsNotNone(PostgresPublishedServices(self.url).get_published(service.benefit.id))

    def test_reject_failed_hold_and_hash_mismatch(self):
        for args in ({"verdict": "revise"}, {"verdict": "hold"}, {"record_sha256": "0" * 64},
                     {"source_sha256": "0" * 64}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                publish_ai_batch(self.url, [self.entry(self.a, **args)])
        self.assertEqual(self.reader.list_published(), ())

    def test_failure_rolls_back_entire_batch_and_existing_release(self):
        publish_policy(self.url, self.a, source_text=source_text(), approved_sha256=record_hash(self.a),
                       reviewer="fictional-human", reviewed_at=AT)
        with self.assertRaisesRegex(ValueError, "ai_catalog_existing_release"):
            publish_ai_batch(self.url, [self.entry(self.b), self.entry(self.a)])
        self.assertIsNotNone(self.reader.get_published(self.a.benefit.id))
        self.assertIsNone(self.reader.get_published(self.b.benefit.id))
        with psycopg.connect(self.url) as connection:
            self.assertEqual(connection.execute("SELECT review_type FROM public.policy_catalog_releases").fetchone()[0], "human")

    def test_sql_constraint_failure_rolls_back_entire_batch(self):
        publish_policy(self.url, self.a, source_text=source_text(), approved_sha256=record_hash(self.a),
                       reviewer="fictional-human", reviewed_at=AT)
        with psycopg.connect(self.url) as connection:
            connection.execute("UPDATE public.policy_catalog_releases SET publication_status='retired' WHERE policy_id=%s", (self.a.benefit.id,))
        with self.assertRaises(psycopg.errors.UniqueViolation):
            publish_ai_batch(self.url, [self.entry(self.b), self.entry(self.a)])
        self.assertIsNone(self.reader.get_published(self.b.benefit.id))
        with psycopg.connect(self.url) as connection:
            self.assertEqual(connection.execute("SELECT publication_status,review_type FROM public.policy_catalog_releases").fetchall(), [("retired", "human")])

    def test_targeted_retirement_is_atomic_and_preserves_human_release(self):
        publish_ai_batch(self.url, [self.entry(self.a)])
        publish_policy(self.url, self.b, source_text=source_text(), approved_sha256=record_hash(self.b),
                       reviewer="fictional-human", reviewed_at=AT)
        correct = (self.a.benefit.id, self.a.benefit.source.document_version, record_hash(self.a))
        with self.assertRaisesRegex(ValueError, "ai_catalog_release_not_current"):
            retire_ai_batch(self.url, [correct, (self.b.benefit.id, self.b.benefit.source.document_version, record_hash(self.b))])
        self.assertIsNotNone(self.reader.get_published(self.a.benefit.id))
        retire_ai_batch(self.url, [correct])
        self.assertIsNone(self.reader.get_published(self.a.benefit.id))
        self.assertIsNotNone(self.reader.get_published(self.b.benefit.id))

    def test_cli_validates_publishes_and_retires_exact_batch(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "record.json").write_text(self.a.model_dump_json(), encoding="utf-8")
            (root / "review.json").write_text(self.review(self.a).model_dump_json(), encoding="utf-8")
            (root / "source.txt").write_text(source_text(), encoding="utf-8")
            (root / "manifest.json").write_text(json.dumps([{"input": "record.json", "review": "review.json", "source": "source.txt"}]), encoding="utf-8")
            with patch.dict("os.environ", {"POLICY_PUBLISH_DATABASE_URL": self.url}):
                self.assertEqual(main(["ai-catalog", "validate", "--manifest", str(root / "manifest.json")]), 0)
                self.assertEqual(main(["ai-catalog", "publish", "--manifest", str(root / "manifest.json")]), 0)
                self.assertIsNotNone(self.reader.get_published(self.a.benefit.id))
                self.assertEqual(main(["ai-catalog", "retire", "--manifest", str(root / "manifest.json")]), 0)
                self.assertIsNone(self.reader.get_published(self.a.benefit.id))

    def test_ai_provenance_cannot_be_mutated(self):
        publish_ai_batch(self.url, [self.entry(self.a)])
        with self.assertRaises(psycopg.errors.RaiseException):
            with psycopg.connect(self.url) as connection:
                connection.execute("UPDATE public.policy_catalog_releases SET ai_review='{}'::jsonb")


if __name__ == "__main__":
    unittest.main()
