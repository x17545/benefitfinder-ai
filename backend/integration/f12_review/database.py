"""Explicit import and SELECT-only preview of approved, still-draft review rows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
import os
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from backend.integration.f12_review.artifacts import ApprovedDocument
from benefitfinder.application.policy_explanations import PublishedClause

ROOT = Path(__file__).resolve().parents[2]

_ITEM_ID_RE = re.compile(r"^(rule|eligibility|amount|timing):[1-9][0-9]*$")


def require_local_test_database(url: str) -> str:
    try:
        config = conninfo_to_dict(url)
        host = config.get("host", "")
        if (
            not config.get("dbname", "").endswith("_test")
            or not (host in {"localhost", "127.0.0.1", "::1"} or host.startswith("/"))
            or "," in host
            or config.get("service") or os.environ.get("PGSERVICE")
            or config.get("hostaddr", os.environ.get("PGHOSTADDR", ""))
            not in {"", "127.0.0.1", "::1"}
        ):
            raise ValueError
    except (ValueError, psycopg.Error):
        raise ValueError("local_dedicated_test_database_required") from None
    return url


def migrate_preview_database(url: str) -> None:
    require_local_test_database(url)
    with psycopg.connect(url) as connection:
        connection.execute((ROOT / "migrations/versions/0001_f13_published_clauses.sql").read_text())
        connection.execute((ROOT / "migrations/testing/0001_f12_review_imports.sql").read_text())


def import_approved_documents(
    url: str, documents: tuple[ApprovedDocument, ...], *, append: bool = False
) -> None:
    require_local_test_database(url)
    if not documents or len({(d.policy_id, d.version) for d in documents}) != len(documents):
        raise ValueError("invalid_preview_documents")
    # Only INSERT: duplicate IDs or conflicting provenance roll back the entire batch.
    with psycopg.connect(url) as connection:
        for doc in documents:
            existing = None
            if append:
                existing = connection.execute(
                    """SELECT policy_name,category,source_url,checked_at,
                              publication_status,published_at
                       FROM public.f13_policy_documents
                       WHERE policy_id=%s AND document_version=%s FOR UPDATE""",
                    (doc.policy_id, doc.version),
                ).fetchone()
            if existing is not None:
                if existing != (doc.name, doc.category, doc.url, doc.checked_at, "draft", None):
                    raise ValueError("invalid_preview_append")
                total, invalid = connection.execute(
                    """SELECT count(*), count(*) FILTER (WHERE
                         c.publication_status<>'draft' OR c.review_status<>'approved'
                         OR c.published_at IS NOT NULL OR i.clause_id IS NULL
                         OR i.raw_policy_id<>%s OR i.content_sha256<>%s)
                       FROM public.f13_clauses c
                       LEFT JOIN public.f13_test_review_imports i
                         USING (policy_id,document_version,clause_id)
                       WHERE c.policy_id=%s AND c.document_version=%s""",
                    (doc.raw_policy_id, doc.content_sha256, doc.policy_id, doc.version),
                ).fetchone()
                if not total or invalid:
                    raise ValueError("invalid_preview_append")
                if connection.execute(
                    """SELECT 1 FROM public.f13_clauses
                       WHERE policy_id=%s AND document_version=%s AND clause_id=ANY(%s)""",
                    (doc.policy_id, doc.version, [item.item_id for item in doc.items]),
                ).fetchone():
                    raise ValueError("invalid_preview_append")
            else:
                connection.execute(
                    """INSERT INTO public.f13_policy_documents
                    (policy_id,document_version,policy_name,category,source_url,checked_at)
                    VALUES (%s,%s,%s,%s,%s,%s)""",
                    (doc.policy_id, doc.version, doc.name, doc.category, doc.url, doc.checked_at),
                )
            for item in doc.items:
                connection.execute(
                    """INSERT INTO public.f13_clauses
                    (policy_id,document_version,clause_id,title,body,source_location,topics,
                     review_status,reviewed_by,reviewed_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,'approved',%s,%s)""",
                    (doc.policy_id, doc.version, item.item_id, item.item_id, item.quote,
                     item.location, [item.kind], item.reviewer, item.reviewed_at),
                )
                connection.execute(
                    """INSERT INTO public.f13_test_review_imports
                    (policy_id,document_version,clause_id,raw_policy_id,pair_sha256,
                     content_sha256,export_sha256,item_kind,approval_reason,candidate)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (doc.policy_id, doc.version, item.item_id, doc.raw_policy_id, doc.pair_sha256,
                     doc.content_sha256, doc.export_sha256, item.kind, item.reason,
                     Jsonb(item.candidate)),
                )


@dataclass(frozen=True)
class PreviewPolicy:
    policy_id: str
    name: str
    category: str
    version: str
    url: str
    checked_at: datetime
    scope: str
    imported_at: datetime


def load_preview_catalog(url: str) -> tuple[PreviewPolicy, ...]:
    require_local_test_database(url)
    with psycopg.connect(url, options="-c default_transaction_read_only=on", row_factory=dict_row) as c:
        rows = c.execute(
            """SELECT d.*, string_agg(i.approval_reason, E'\\n' ORDER BY i.clause_id) AS scope,
                max(i.imported_at) AS imported_at
            FROM public.f13_policy_documents d
            JOIN public.f13_test_review_imports i USING (policy_id,document_version)
            JOIN public.f13_clauses c USING (policy_id,document_version,clause_id)
            WHERE d.publication_status = 'draft' AND c.publication_status='draft'
              AND c.review_status='approved'
            GROUP BY d.policy_id,d.document_version ORDER BY d.policy_id"""
        ).fetchall()
    return tuple(PreviewPolicy(
        r["policy_id"], r["policy_name"], r["category"], r["document_version"],
        r["source_url"], r["checked_at"], r["scope"], r["imported_at"],
    ) for r in rows)


@dataclass(frozen=True)
class DraftEvidence:
    clauses: tuple[PublishedClause, ...]
    scope: str


async def load_approved_draft_evidence(
    url: str, policy: PreviewPolicy, item_ids: tuple[str, ...] = ()
) -> DraftEvidence:
    require_local_test_database(url)
    if not isinstance(item_ids, tuple) or len(item_ids) > 5 or any(
        not isinstance(value, str) or not _ITEM_ID_RE.fullmatch(value) for value in item_ids
    ) or len(set(item_ids)) != len(item_ids):
        raise ValueError("invalid_preview_item_selection")
    selected = list(item_ids) if item_ids else None
    async with await psycopg.AsyncConnection.connect(
        url, connect_timeout=2,
        options="-c default_transaction_read_only=on -c statement_timeout=1000 -c lock_timeout=500",
        row_factory=dict_row,
    ) as connection:
        cursor = await connection.execute(
            """SELECT c.*, d.source_url,d.checked_at,i.approval_reason,i.item_kind
            FROM public.f13_clauses c JOIN public.f13_policy_documents d
            USING (policy_id,document_version)
            JOIN public.f13_test_review_imports i USING (policy_id,document_version,clause_id)
            WHERE c.policy_id=%s AND c.document_version=%s AND d.category=%s
              AND d.publication_status='draft' AND c.publication_status='draft'
              AND c.review_status='approved'
              AND (%s::text[] IS NULL OR c.clause_id=ANY(%s::text[]))
            ORDER BY CASE i.item_kind WHEN 'timing' THEN 0 WHEN 'eligibility' THEN 1
                     WHEN 'rule' THEN 2 ELSE 3 END, c.clause_id LIMIT 100""",
            (policy.policy_id, policy.version, policy.category, selected, selected),
        )
        rows = await cursor.fetchall()
    if selected is not None and {r["clause_id"] for r in rows} != set(selected):
        raise ValueError("invalid_preview_item_selection")
    clauses = tuple(PublishedClause(
        id=r["clause_id"], policy_id=r["policy_id"], document_version=r["document_version"],
        title={"timing": "승인된 지급 시점과 지급 조건", "eligibility": "승인된 자격 관련 근거",
               "rule": "승인된 중복 제한과 예외", "amount": "승인된 명시 금액의 근거"}[r["item_kind"]],
        text=r["body"], source_url=r["source_url"],
        topics=tuple(r["topics"]), published=False, location=r["source_location"],
        checked_at=r["checked_at"],
    ) for r in rows[:5])
    return DraftEvidence(clauses, "\n".join(r["approval_reason"] for r in rows))
