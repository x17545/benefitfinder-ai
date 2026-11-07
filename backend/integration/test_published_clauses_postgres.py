"""Actual PostgreSQL migration/read/write/failure checks; no SQLite or real notices."""

from __future__ import annotations

import asyncio
import json
import time
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock

import psycopg
from psycopg.conninfo import make_conninfo
from fastapi.testclient import TestClient

from backend.integration.postgres_fixture import (
    CHECKED_AT,
    MIGRATION,
    migrate_test_database,
    seed_test_database,
    test_database_url,
)
from benefitfinder.application.policy_explanations import GeneratedExplanation, explain_policy
from benefitfinder.bootstrap import create_app
from benefitfinder.infrastructure.database.repositories.published_clauses import (
    PostgresPublishedClauses,
)
from benefitfinder.infrastructure.example_explanation_data import recommendation_explanation_data


class PostgresPublishedClausesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.url = test_database_url()
        migrate_test_database(cls.url)

    def setUp(self) -> None:
        seed_test_database(self.url)
        self.policy = recommendation_explanation_data()[0][0]
        self.repository = PostgresPublishedClauses(self.url)

    def search(self, **overrides: object) -> tuple:
        return asyncio.run(self.repository.search(replace(self.policy, **overrides), limit=5))

    def execute(self, statement: str, params: tuple = ()) -> None:
        with psycopg.connect(self.url) as connection:
            connection.execute(statement, params)

    def test_migration_record_atomic_reapplication_and_constraints(self) -> None:
        with psycopg.connect(self.url) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT version FROM public.benefitfinder_schema_migrations"
                ).fetchall(),
                [("0001_f13_published_clauses",)],
            )
            with self.assertRaises(psycopg.errors.DuplicateTable), connection.transaction():
                connection.execute(MIGRATION.read_text())
            self.assertEqual(
                connection.execute("SELECT count(*) FROM public.f13_clauses").fetchone()[0], 6
            )
        with self.assertRaises(psycopg.errors.CheckViolation):
            self.execute("UPDATE public.f13_clauses SET review_status='rejected'")
        with self.assertRaises(psycopg.errors.CheckViolation):
            self.execute("UPDATE public.f13_clauses SET reviewed_by=NULL")
        with self.assertRaises(psycopg.errors.CheckViolation):
            self.execute("UPDATE public.f13_clauses SET reviewed_by='   '")
        with self.assertRaises(psycopg.errors.CheckViolation):
            self.execute("UPDATE public.f13_clauses SET topics=ARRAY['application',NULL]::text[]")
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            self.execute("UPDATE public.f13_clauses SET document_version='missing'")

    def test_same_policy_version_category_and_real_source_metadata(self) -> None:
        clauses = self.search()
        self.assertTrue(clauses)
        for clause in clauses:
            self.assertEqual(clause.policy_id, self.policy.policy_id)
            self.assertEqual(clause.document_version, self.policy.document_version)
            self.assertEqual(clause.checked_at, CHECKED_AT)
            self.assertTrue(clause.location.startswith("가상 안내 /"))
        self.assertEqual(self.search(document_version="missing"), ())
        self.assertEqual(self.search(category="employment"), ())
        self.assertEqual(self.search(policy_id="youth:missing"), ())

    def test_approved_but_unpublished_pending_rejected_and_retired_are_excluded(self) -> None:
        for review, publication in [
            ("approved", "draft"),
            ("pending", "draft"),
            ("rejected", "draft"),
            ("approved", "retired"),
        ]:
            with self.subTest(review=review, publication=publication):
                seed_test_database(self.url)
                self.execute(
                    "UPDATE public.f13_clauses SET review_status=%s, "
                    "publication_status=%s WHERE policy_id=%s",
                    (review, publication, self.policy.policy_id),
                )
                self.assertEqual(self.search(), ())
        seed_test_database(self.url)
        self.execute(
            "UPDATE public.f13_policy_documents SET publication_status='retired' "
            "WHERE policy_id=%s",
            (self.policy.policy_id,),
        )
        self.assertEqual(self.search(), ())

    def test_top_five_stable_ranking_and_changes_visible_without_cache(self) -> None:
        with psycopg.connect(self.url) as connection:
            for index in range(8):
                connection.execute(
                    """INSERT INTO public.f13_clauses SELECT policy_id,document_version,%s,
                       title,body,source_location,ARRAY['application','amount']::text[],
                       review_status,reviewed_by,reviewed_at,publication_status,published_at
                       FROM public.f13_clauses WHERE clause_id='P1-application'""",
                    (f"extra-{index}",),
                )
        self.assertEqual([c.id for c in self.search()], [f"extra-{i}" for i in range(5)])
        self.execute(
            "UPDATE public.f13_clauses SET publication_status='retired' WHERE clause_id='extra-0'"
        )
        self.assertEqual([c.id for c in self.search()], [f"extra-{i}" for i in range(1, 6)])

    def test_document_rollover_hides_old_version(self) -> None:
        self.execute(
            "UPDATE public.f13_policy_documents SET publication_status='retired' "
            "WHERE policy_id=%s",
            (self.policy.policy_id,),
        )
        self.execute(
            """INSERT INTO public.f13_policy_documents SELECT policy_id,'fictional-v2',
                     policy_name,category,source_url,checked_at,'published',published_at
                     FROM public.f13_policy_documents WHERE policy_id=%s""",
            (self.policy.policy_id,),
        )
        self.execute(
            """INSERT INTO public.f13_clauses SELECT policy_id,'fictional-v2',clause_id,
                     title,body,source_location,topics,review_status,reviewed_by,reviewed_at,
                     publication_status,published_at FROM public.f13_clauses WHERE policy_id=%s""",
            (self.policy.policy_id,),
        )
        self.assertEqual(self.search(), ())
        self.assertTrue(self.search(document_version="fictional-v2"))

    def test_real_connection_failure_and_db_lock_timeout_keep_fallback(self) -> None:
        bad = PostgresPublishedClauses("postgresql://missing@127.0.0.1:1/unavailable")
        result = asyncio.run(explain_policy(self.policy, bad))
        self.assertEqual(result.fallback_reason, "retrieval_failed")
        with psycopg.connect(self.url) as locker:
            locker.execute("LOCK TABLE public.f13_clauses IN ACCESS EXCLUSIVE MODE")
            started = time.monotonic()
            result = asyncio.run(explain_policy(self.policy, self.repository))
            self.assertLess(time.monotonic() - started, 2.5)
            self.assertFalse(result.ai_generated)
            self.assertEqual(result.fallback_reason, "timeout")
        self.assertTrue(self.search())

    def test_api_uses_db_metadata_and_preserves_calculation_when_db_empty_or_down(self) -> None:
        payload = json.loads(
            (MIGRATION.parents[3] / "contracts/fixtures/recommendation-request.json").read_text()
        )
        generator = AsyncMock()
        generator.generate.side_effect = lambda request, clauses, **kwargs: GeneratedExplanation(
            "신청 안내를 확인해요.", (clauses[0].id,)
        )
        with TestClient(
            create_app(explanation_database_url=self.url, explanation_generator=generator)
        ) as client:
            result = client.post("/api/v1/recommendations", json=payload).json()
            self.assertTrue(result["ai_generated"])
            rows = result["results"]["1y"]["pv_off"]["portfolios"][0]
            for policy in rows["policies"]:
                evidence = policy["ai_sources"][0]
                self.assertTrue(evidence["location"].startswith("가상 안내 /"))
                self.assertEqual(evidence["source"]["checked_at"], CHECKED_AT.isoformat())
            self.execute("UPDATE public.f13_policy_documents SET publication_status='retired'")
            fallback = client.post("/api/v1/recommendations", json=payload).json()
            fallback_rows = fallback["results"]["1y"]["pv_off"]["portfolios"][0]
            self.assertFalse(fallback["ai_generated"])
            self.assertEqual(fallback_rows["total_won"], rows["total_won"])
            self.assertTrue(all(not p["ai_sources"] for p in fallback_rows["policies"]))

    def test_runtime_reads_in_a_read_only_transaction(self) -> None:
        with psycopg.connect(self.url) as owner:
            if (
                owner.execute("SELECT 1 FROM pg_roles WHERE rolname='f13_test_reader'").fetchone()
                is None
            ):
                owner.execute("CREATE ROLE f13_test_reader LOGIN PASSWORD 'fictional-test-only'")
            owner.execute("GRANT USAGE ON SCHEMA public TO f13_test_reader")
            owner.execute(
                "GRANT SELECT ON public.f13_policy_documents,public.f13_clauses TO f13_test_reader"
            )
        reader_url = make_conninfo(self.url, user="f13_test_reader", password="fictional-test-only")
        reader = PostgresPublishedClauses(reader_url)
        self.assertTrue(asyncio.run(reader.search(self.policy, limit=5)))
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with psycopg.connect(reader_url) as connection:
                connection.execute("DELETE FROM public.f13_clauses WHERE false")
