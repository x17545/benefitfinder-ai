"""Actual PostgreSQL publication, version changes, constraints and HTTP reads."""

from __future__ import annotations

import json
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

from backend.integration.postgres_fixture import migrate_test_database, test_database_url
from backend.tests.catalog_fixtures import release_data, source_text
from benefitfinder.application.published_policies import PolicyCatalogUnavailable
from benefitfinder.bootstrap import create_app
from benefitfinder.infrastructure.database.repositories.published_policies import (
    PostgresPublishedPolicies,
    publish_policy,
)
from benefitfinder.infrastructure.policy_catalog_records import CatalogRecord, record_hash


class PublishedPoliciesPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.url = test_database_url()
        migrate_test_database(cls.url)

    def setUp(self):
        with psycopg.connect(self.url) as connection:
            connection.execute("TRUNCATE public.policy_catalog_releases")
        self.repository = PostgresPublishedPolicies(self.url)

    def publish(self, data):
        record = CatalogRecord.model_validate(data)
        publish_policy(
            self.url,
            record,
            source_text=source_text(record.benefit.benefit_amount.amount_won),
            approved_sha256=record_hash(record),
            reviewer="fictional-catalog-reviewer",
            reviewed_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
        )
        return record

    def test_published_record_is_read_by_api_and_no_example_policies_leak(self):
        self.publish(release_data("A"))
        self.publish(release_data("B", amount=2_000_000))
        rows = self.repository.list_published()
        self.assertEqual([p.policy_id for p in rows], ["youth:catalog:A", "youth:catalog:B"])
        root = Path(__file__).resolve().parents[2]
        payload = json.loads((root / "contracts/fixtures/recommendation-request.json").read_text())
        payload["profile"]["age"] = 25
        payload["profile"]["domain_data"]["interests"] = ["housing"]
        with TestClient(create_app(policy_database_url=self.url)) as client:
            response = client.post("/api/v1/recommendations", json=payload)
            self.assertEqual(response.status_code, 200, response.text)
            portfolio = response.json()["results"]["1y"]["pv_off"]["portfolios"][0]
            self.assertEqual(portfolio["total_won"], 3_000_000)
            detail = client.get("/api/v1/benefits/youth:catalog:A")
            self.assertEqual(detail.json()["source"]["document_version"], "fictional-v1")
            self.assertEqual(client.get("/api/v1/benefits/youth:example:P1").status_code, 404)

    def test_pending_rejected_unpublished_retired_and_stale_approval_are_excluded(self):
        self.publish(release_data())
        for review, publication in [
            ("approved", "draft"),
            ("pending", "draft"),
            ("rejected", "draft"),
            ("approved", "retired"),
        ]:
            with self.subTest(review=review, publication=publication):
                with psycopg.connect(self.url) as connection:
                    connection.execute(
                        "UPDATE public.policy_catalog_releases SET review_status=%s,publication_status=%s",
                        (review, publication),
                    )
                self.assertEqual(self.repository.list_published(), ())
        with psycopg.connect(self.url) as connection:
            connection.execute(
                "UPDATE public.policy_catalog_releases SET review_status='approved',publication_status='published',approved_sha256=%s",
                ("0" * 64,),
            )
        with self.assertRaises(PolicyCatalogUnavailable):
            self.repository.list_published()

    def test_version_rollover_and_duplicate_failure_are_atomic(self):
        self.publish(release_data())
        newer = json.loads(json.dumps(release_data()).replace("fictional-v1", "fictional-v2"))
        self.publish(newer)
        self.assertEqual(
            self.repository.get_published("youth:catalog:A").detail["source"]["document_version"],
            "fictional-v2",
        )
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.publish(newer)
        self.assertEqual(len(self.repository.list_published()), 1)
        self.assertEqual(
            self.repository.get_published("youth:catalog:A").detail["source"]["document_version"],
            "fictional-v2",
        )
        with psycopg.connect(self.url) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT publication_status FROM public.policy_catalog_releases ORDER BY document_version"
                ).fetchall(),
                [("retired",), ("published",)],
            )

    def test_approval_and_source_changes_fail_before_any_retirement(self):
        record = self.publish(release_data())
        for digest, text in [
            ("0" * 64, source_text()),
            (record_hash(record), source_text() + "changed"),
        ]:
            with self.assertRaises(ValueError):
                publish_policy(
                    self.url,
                    record,
                    source_text=text,
                    approved_sha256=digest,
                    reviewer="fictional-reviewer",
                    reviewed_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
                )
            self.assertEqual(len(self.repository.list_published()), 1)

    def test_database_constraints_reject_unapproved_publish_and_wrong_record_identity(self):
        record = self.publish(release_data())
        mutations = [
            ("UPDATE public.policy_catalog_releases SET reviewed_by=NULL", ()),
            ("UPDATE public.policy_catalog_releases SET review_status='rejected'", ()),
            ("UPDATE public.policy_catalog_releases SET payload=%s", (Jsonb({}),)),
            ("UPDATE public.policy_catalog_releases SET document_version='other'", ()),
        ]
        for sql, params in mutations:
            with self.subTest(sql=sql), self.assertRaises(psycopg.errors.CheckViolation):
                with psycopg.connect(self.url) as connection:
                    connection.execute(sql, params)
        self.assertEqual(
            self.repository.get_published(record.benefit.id).policy_id, record.benefit.id
        )

    def test_read_role_has_no_write_permission_and_lock_failure_is_bounded(self):
        self.publish(release_data())
        with psycopg.connect(self.url) as owner:
            if (
                owner.execute(
                    "SELECT 1 FROM pg_roles WHERE rolname='catalog_test_reader'"
                ).fetchone()
                is None
            ):
                owner.execute(
                    "CREATE ROLE catalog_test_reader LOGIN PASSWORD 'fictional-test-only'"
                )
            owner.execute("GRANT USAGE ON SCHEMA public TO catalog_test_reader")
            owner.execute("GRANT SELECT ON public.policy_catalog_releases TO catalog_test_reader")
        url = make_conninfo(self.url, user="catalog_test_reader", password="fictional-test-only")
        self.assertEqual(len(PostgresPublishedPolicies(url).list_published()), 1)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with psycopg.connect(url) as connection:
                connection.execute("DELETE FROM public.policy_catalog_releases WHERE false")
        with psycopg.connect(self.url) as locker:
            locker.execute("LOCK TABLE public.policy_catalog_releases IN ACCESS EXCLUSIVE MODE")
            started = time.monotonic()
            with self.assertRaises(PolicyCatalogUnavailable):
                self.repository.list_published()
            self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(len(self.repository.list_published()), 1)


if __name__ == "__main__":
    unittest.main()
