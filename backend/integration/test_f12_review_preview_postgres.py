"""A separate disposable PostgreSQL database; fictional approvals only."""

import asyncio
import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from backend.integration.f12_review.artifacts import load_approved_export
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


_FIXTURE_AT = datetime(2000, 1, 1, tzinfo=timezone.utc)


def _fictional_version() -> tuple[str, str]:
    digest = hashlib.sha256(b"fictional source").hexdigest()
    return digest, "sha256:" + digest


def _payload_with_specs(policy_id: str, specs: tuple[tuple[str, str], ...]) -> dict:
    digest, version = _fictional_version()
    items: list[dict] = []
    for item_id, kind in specs:
        evidence = {
            "document_id": "virtual-notice",
            "document_version": version,
            "content_sha256": digest,
            "location": f"line {item_id}",
            "quote": f"가상 안내 {item_id} 내용을 확인합니다.",
        }
        if kind == "rule":
            candidate: dict = {
                "source_policy_id": policy_id,
                "citations": [evidence],
            }
        else:
            candidate = {"evidence": evidence}
        items.append(
            {
                "item_id": item_id,
                "kind": kind,
                "approval": {
                    "reviewer": "fictional-reviewer",
                    "reason": f"가상 {item_id} 승인",
                    "reviewed_at": _FIXTURE_AT.isoformat(),
                },
                "candidate": candidate,
            }
        )
    return {
        "format_version": 1,
        "kind": "f12_approved_review_export",
        "pair_sha256": "a" * 64,
        "policy_id": policy_id,
        "source": {
            "id": "virtual-notice",
            "url": "https://policy.example/fictional-review",
            "document_version": version,
            "content_sha256": digest,
            "collected_at": _FIXTURE_AT.isoformat(),
        },
        "items": items,
    }


def _load_payload(payload: dict, *, name: str = "가상 검수 정책", category: str = "employment"):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "approved.json"
        path.write_text(json.dumps(payload, ensure_ascii=False))
        return load_approved_export(path, name=name, category=category)


def _snapshot_rows(url: str, policy_id: str, version: str):
    with psycopg.connect(url) as connection:
        doc = connection.execute(
            """SELECT policy_id, document_version, policy_name, category, source_url,
                      checked_at, publication_status, published_at
               FROM public.f13_policy_documents WHERE policy_id=%s AND document_version=%s""",
            (policy_id, version),
        ).fetchall()
        clauses = connection.execute(
            """SELECT policy_id, document_version, clause_id, title, body, source_location,
                      topics, review_status, reviewed_by, reviewed_at,
                      publication_status, published_at
               FROM public.f13_clauses WHERE policy_id=%s AND document_version=%s
               ORDER BY clause_id""",
            (policy_id, version),
        ).fetchall()
        ledger = connection.execute(
            """SELECT policy_id, document_version, clause_id, raw_policy_id, pair_sha256,
                      content_sha256, export_sha256, item_kind, approval_reason, candidate,
                      imported_at
               FROM public.f13_test_review_imports WHERE policy_id=%s AND document_version=%s
               ORDER BY clause_id""",
            (policy_id, version),
        ).fetchall()
    return doc, clauses, ledger


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

    def test_strict_mode_rejects_same_document_even_with_new_ids(self):
        payload = _payload_with_specs(
            self.doc.raw_policy_id, (("eligibility:1", "eligibility"),)
        )
        new_doc = _load_payload(payload, name=self.doc.name, category=self.doc.category)
        self.assertEqual(new_doc.policy_id, self.doc.policy_id)
        self.assertEqual(new_doc.version, self.doc.version)
        with self.assertRaises(psycopg.errors.UniqueViolation):
            import_approved_documents(self.url, (new_doc,))
        with psycopg.connect(self.url) as connection:
            self.assertEqual(
                connection.execute("SELECT count(*) FROM public.f13_clauses").fetchone()[0], 1
            )
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM public.f13_test_review_imports"
                ).fetchone()[0],
                1,
            )

    def test_append_reuses_document_and_preserves_existing_rows(self):
        before_doc, before_clauses, before_ledger = _snapshot_rows(
            self.url, self.doc.policy_id, self.doc.version
        )
        self.assertEqual(len(before_clauses), 1)
        payload = _payload_with_specs(
            self.doc.raw_policy_id,
            (("eligibility:1", "eligibility"), ("amount:1", "amount")),
        )
        new_doc = _load_payload(payload, name=self.doc.name, category=self.doc.category)
        import_approved_documents(self.url, (new_doc,), append=True)
        with psycopg.connect(self.url) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM public.f13_policy_documents"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute("SELECT count(*) FROM public.f13_clauses").fetchone()[0], 3
            )
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM public.f13_test_review_imports"
                ).fetchone()[0],
                3,
            )
        after_doc, after_clauses, after_ledger = _snapshot_rows(
            self.url, self.doc.policy_id, self.doc.version
        )
        self.assertEqual(before_doc, after_doc)
        old_ids = {row[2] for row in before_clauses}
        self.assertEqual(old_ids, {"timing:1"})
        before_by_id = {row[2]: row for row in before_clauses}
        after_by_id = {row[2]: row for row in after_clauses}
        for clause_id, row in before_by_id.items():
            self.assertEqual(row, after_by_id[clause_id])
        before_ledger_by_id = {row[2]: row for row in before_ledger}
        after_ledger_by_id = {row[2]: row for row in after_ledger}
        for clause_id, row in before_ledger_by_id.items():
            self.assertEqual(row, after_ledger_by_id[clause_id])
        request = ExplanationRequest(
            self.doc.policy_id, self.doc.name, self.doc.category, self.doc.version, None
        )
        self.assertEqual(asyncio.run(PostgresPublishedClauses(self.url).search(request, limit=5)), ())
        policy = load_preview_catalog(self.url)[0]
        evidence = asyncio.run(load_approved_draft_evidence(self.url, policy))
        self.assertEqual({clause.id for clause in evidence.clauses}, {"timing:1", "eligibility:1", "amount:1"})

    def test_append_duplicate_item_rolls_back(self):
        before = _snapshot_rows(self.url, self.doc.policy_id, self.doc.version)
        payload = _payload_with_specs(
            self.doc.raw_policy_id,
            (("timing:1", "timing"), ("eligibility:1", "eligibility")),
        )
        new_doc = _load_payload(payload, name=self.doc.name, category=self.doc.category)
        with self.assertRaises(ValueError):
            import_approved_documents(self.url, (new_doc,), append=True)
        after = _snapshot_rows(self.url, self.doc.policy_id, self.doc.version)
        self.assertEqual(before, after)
        with psycopg.connect(self.url) as connection:
            self.assertEqual(
                connection.execute("SELECT count(*) FROM public.f13_clauses").fetchone()[0], 1
            )

    def test_append_metadata_mismatch_rolls_back(self):
        payload = _payload_with_specs(
            self.doc.raw_policy_id, (("eligibility:1", "eligibility"),)
        )
        renamed = _load_payload(payload, name="다른 가상 정책", category=self.doc.category)
        with self.assertRaises(ValueError):
            import_approved_documents(self.url, (renamed,), append=True)
        other_category = "housing" if self.doc.category == "employment" else "employment"
        recategorized = _load_payload(payload, name=self.doc.name, category=other_category)
        with self.assertRaises(ValueError):
            import_approved_documents(self.url, (recategorized,), append=True)
        with psycopg.connect(self.url) as connection:
            self.assertEqual(
                connection.execute("SELECT count(*) FROM public.f13_clauses").fetchone()[0], 1
            )
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM public.f13_test_review_imports"
                ).fetchone()[0],
                1,
            )

    def test_append_published_document_rolls_back(self):
        with psycopg.connect(self.url) as connection:
            connection.execute(
                "UPDATE public.f13_policy_documents SET publication_status='published', "
                "published_at=checked_at"
            )
        payload = _payload_with_specs(
            self.doc.raw_policy_id, (("eligibility:1", "eligibility"),)
        )
        new_doc = _load_payload(payload, name=self.doc.name, category=self.doc.category)
        with self.assertRaises(ValueError):
            import_approved_documents(self.url, (new_doc,), append=True)
        with psycopg.connect(self.url) as connection:
            self.assertEqual(
                connection.execute("SELECT count(*) FROM public.f13_clauses").fetchone()[0], 1
            )
            self.assertIsNone(
                connection.execute(
                    "SELECT 1 FROM public.f13_clauses WHERE clause_id='eligibility:1'"
                ).fetchone()
            )

    def test_append_diverged_clause_rolls_back(self):
        with psycopg.connect(self.url) as connection:
            connection.execute("UPDATE public.f13_clauses SET review_status='pending'")
        payload = _payload_with_specs(
            self.doc.raw_policy_id, (("eligibility:1", "eligibility"),)
        )
        new_doc = _load_payload(payload, name=self.doc.name, category=self.doc.category)
        with self.assertRaises(ValueError):
            import_approved_documents(self.url, (new_doc,), append=True)
        with psycopg.connect(self.url) as connection:
            self.assertEqual(
                connection.execute("SELECT count(*) FROM public.f13_clauses").fetchone()[0], 1
            )

    def test_append_inserts_missing_document(self):
        payload = _payload_with_specs("virtual-second", (("timing:1", "timing"),))
        new_doc = _load_payload(payload)
        import_approved_documents(self.url, (new_doc,), append=True)
        with psycopg.connect(self.url) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM public.f13_policy_documents"
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                connection.execute("SELECT count(*) FROM public.f13_clauses").fetchone()[0], 2
            )

    def test_item_ids_selection_returns_only_requested_before_limit(self):
        with psycopg.connect(self.url) as connection:
            connection.execute(
                "TRUNCATE public.f13_test_review_imports, public.f13_clauses, "
                "public.f13_policy_documents"
            )
        specs = (
            ("timing:1", "timing"),
            ("timing:2", "timing"),
            ("eligibility:1", "eligibility"),
            ("eligibility:2", "eligibility"),
            ("rule:1", "rule"),
            ("amount:1", "amount"),
        )
        payload = _payload_with_specs("virtual-approved", specs)
        doc6 = _load_payload(payload)
        import_approved_documents(self.url, (doc6,))
        policy = load_preview_catalog(self.url)[0]
        default = asyncio.run(load_approved_draft_evidence(self.url, policy))
        self.assertEqual(len(default.clauses), 5)
        self.assertNotIn("amount:1", {clause.id for clause in default.clauses})
        selected = asyncio.run(
            load_approved_draft_evidence(self.url, policy, ("amount:1",))
        )
        self.assertEqual({clause.id for clause in selected.clauses}, {"amount:1"})
        self.assertIn("가상 amount:1 승인", selected.scope)
        self.assertNotIn("가상 timing:1 승인", selected.scope)
        multi = asyncio.run(
            load_approved_draft_evidence(self.url, policy, ("amount:1", "eligibility:2"))
        )
        self.assertEqual(
            {clause.id for clause in multi.clauses}, {"amount:1", "eligibility:2"}
        )
        self.assertIn("가상 amount:1 승인", multi.scope)
        self.assertIn("가상 eligibility:2 승인", multi.scope)
        self.assertNotIn("가상 timing:1 승인", multi.scope)

    def test_item_ids_selection_missing_or_unapproved_fails(self):
        policy = load_preview_catalog(self.url)[0]
        with self.assertRaises(ValueError):
            asyncio.run(load_approved_draft_evidence(self.url, policy, ("timing:99",)))
        with self.assertRaises(ValueError):
            asyncio.run(
                load_approved_draft_evidence(self.url, policy, ("timing:1", "timing:1"))
            )
        with self.assertRaises(ValueError):
            asyncio.run(load_approved_draft_evidence(self.url, policy, ("bad-id",)))
        with self.assertRaises(ValueError):
            asyncio.run(
                load_approved_draft_evidence(
                    self.url,
                    policy,
                    ("timing:1", "timing:2", "eligibility:1", "eligibility:2", "rule:1", "amount:1"),
                )
            )
        with psycopg.connect(self.url) as connection:
            connection.execute("UPDATE public.f13_clauses SET review_status='pending'")
        with self.assertRaises(ValueError):
            asyncio.run(load_approved_draft_evidence(self.url, policy, ("timing:1",)))
        with psycopg.connect(self.url) as connection:
            connection.execute(
                "UPDATE public.f13_clauses SET review_status='approved', "
                "publication_status='published', published_at=reviewed_at"
            )
        with self.assertRaises(ValueError):
            asyncio.run(load_approved_draft_evidence(self.url, policy, ("timing:1",)))


    def test_append_checks_every_existing_clause_provenance(self):
        second = _load_payload(_payload_with_specs(
            self.doc.raw_policy_id, (("amount:1", "amount"),)
        ))
        incoming = _load_payload(_payload_with_specs(
            self.doc.raw_policy_id, (("amount:2", "amount"),)
        ))
        import_approved_documents(self.url, (second,), append=True)
        for field, invalid, original in (
            ("content_sha256", "b" * 64, self.doc.content_sha256),
            ("raw_policy_id", "other-fictional-policy", self.doc.raw_policy_id),
        ):
            with self.subTest(field=field):
                statement = sql.SQL("UPDATE public.f13_test_review_imports SET {}=%s "
                                    "WHERE clause_id='amount:1'").format(sql.Identifier(field))
                with psycopg.connect(self.url) as connection:
                    connection.execute(statement, (invalid,))
                before = _snapshot_rows(self.url, self.doc.policy_id, self.doc.version)
                with self.assertRaisesRegex(ValueError, "invalid_preview_append"):
                    import_approved_documents(self.url, (incoming,), append=True)
                self.assertEqual(before, _snapshot_rows(self.url, self.doc.policy_id, self.doc.version))
                with psycopg.connect(self.url) as connection:
                    connection.execute(statement, (original,))
        with psycopg.connect(self.url) as connection:
            connection.execute("DELETE FROM public.f13_test_review_imports WHERE clause_id='amount:1'")
        before = _snapshot_rows(self.url, self.doc.policy_id, self.doc.version)
        with self.assertRaisesRegex(ValueError, "invalid_preview_append"):
            import_approved_documents(self.url, (incoming,), append=True)
        self.assertEqual(before, _snapshot_rows(self.url, self.doc.policy_id, self.doc.version))


if __name__ == "__main__":
    unittest.main()
