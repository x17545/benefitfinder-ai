"""A separate disposable PostgreSQL database; fictional approvals only."""

import asyncio
import unittest
from dataclasses import replace
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from backend.integration.f12_review.database import (
    import_approved_documents,
    load_approved_draft_evidence,
    load_preview_catalog,
    migrate_preview_database,
    require_local_test_database,
)
from backend.integration.postgres_fixture import test_database_url
from backend.tests.test_f12_review_preview import read_virtual_export, virtual_export
from benefitfinder.application.policy_explanations import ExplanationRequest
from benefitfinder.infrastructure.database.repositories.published_clauses import PostgresPublishedClauses


class F12ReviewPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = require_local_test_database(test_database_url())
        cls.name = "bf_review_" + uuid4().hex[:12] + "_test"
        with psycopg.connect(cls.base, autocommit=True) as c:
            c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(cls.name)))
        cls.url = make_conninfo(cls.base, dbname=cls.name)
        migrate_preview_database(cls.url)

    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(cls.base, autocommit=True) as c:
            c.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(cls.name)))

    def setUp(self):
        with psycopg.connect(self.url) as c:
            c.execute("TRUNCATE public.f13_test_review_imports, public.f13_clauses, "
                      "public.f13_policy_documents")
        self.doc = read_virtual_export(virtual_export())
        import_approved_documents(self.url, (self.doc,))

    def test_preserves_approval_provenance_but_never_publishes(self):
        with psycopg.connect(self.url) as c:
            self.assertEqual(c.execute(
                "SELECT review_status,publication_status,reviewed_by,published_at "
                "FROM public.f13_clauses"
            ).fetchall(), [("approved", "draft", "fictional-reviewer", None)])
            self.assertEqual(c.execute(
                "SELECT publication_status,published_at FROM public.f13_policy_documents"
            ).fetchall(), [("draft", None)])
            row = c.execute("SELECT raw_policy_id,pair_sha256,approval_reason,candidate "
                            "FROM public.f13_test_review_imports").fetchone()
            self.assertEqual(row, (self.doc.raw_policy_id, self.doc.pair_sha256,
                                   self.doc.items[0].reason, self.doc.items[0].candidate))

    def test_production_reader_excludes_all_rows_preview_keeps_exact_citation(self):
        request = ExplanationRequest(self.doc.policy_id, self.doc.name, self.doc.category,
                                     self.doc.version, None)
        self.assertEqual(asyncio.run(PostgresPublishedClauses(self.url).search(request, limit=5)), ())
        policy = load_preview_catalog(self.url)[0]
        clauses = asyncio.run(load_approved_draft_evidence(self.url, policy)).clauses
        self.assertEqual(len(clauses), 1)
        self.assertFalse(clauses[0].published)
        self.assertEqual(clauses[0].text, self.doc.items[0].quote)
        self.assertEqual(asyncio.run(load_approved_draft_evidence(
            self.url, replace(policy, version="different-version"))).clauses, ())

    def test_bulk_duplicate_rolls_back_without_replacing_previous_records(self):
        second = read_virtual_export(virtual_export("virtual-second"))
        with self.assertRaises(psycopg.errors.UniqueViolation):
            import_approved_documents(self.url, (second, self.doc))
        with psycopg.connect(self.url) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM public.f13_policy_documents").fetchone()[0], 1)

    def test_pending_or_published_rows_are_not_preview_evidence(self):
        policy = load_preview_catalog(self.url)[0]
        with psycopg.connect(self.url) as c:
            c.execute("UPDATE public.f13_clauses SET review_status='pending'")
        self.assertEqual(asyncio.run(load_approved_draft_evidence(self.url, policy)).clauses, ())
        self.assertEqual(load_preview_catalog(self.url), ())
        with psycopg.connect(self.url) as c:
            c.execute("UPDATE public.f13_clauses SET review_status='approved', "
                      "publication_status='published', published_at=reviewed_at")
        self.assertEqual(asyncio.run(load_approved_draft_evidence(self.url, policy)).clauses, ())
        self.assertEqual(load_preview_catalog(self.url), ())


if __name__ == "__main__":
    unittest.main()
