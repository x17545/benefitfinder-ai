"""Fictional F13 fixtures in a dedicated *_test database; never imports F12 output."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict

from benefitfinder.infrastructure.example_explanation_data import recommendation_explanation_data

MIGRATION = (
    Path(__file__).resolve().parents[1] / "migrations/versions/0001_f13_published_clauses.sql"
)
CHECKED_AT = datetime(2026, 11, 7, 1, 30, tzinfo=timezone.utc)


def test_database_url() -> str:
    """Refuse missing config and non-test DBs before executing test DDL/DML."""
    url = os.environ.get("F13_TEST_DATABASE_URL", "")
    try:
        name = conninfo_to_dict(url).get("dbname", "")
    except psycopg.Error:
        raise ValueError("invalid_F13_TEST_DATABASE_URL") from None
    if not url or not name.endswith("_test"):
        raise ValueError("F13_TEST_DATABASE_URL_must_target_dedicated_test_database")
    return url


def migrate_test_database(url: str) -> None:
    """Apply once, recording the version, in the same explicit atomic transaction."""
    with psycopg.connect(url) as connection:
        installed = connection.execute(
            "SELECT to_regclass('public.benefitfinder_schema_migrations')"
        ).fetchone()[0]
        if installed is None:
            connection.execute(MIGRATION.read_text())
        elif (
            connection.execute(
                "SELECT version FROM public.benefitfinder_schema_migrations WHERE version = %s",
                ("0001_f13_published_clauses",),
            ).fetchone()
            is None
        ):
            raise ValueError("unexpected_test_schema_version")


def seed_test_database(url: str) -> None:
    """Reset only the F13 tables in this explicitly selected fictional test DB."""
    if not conninfo_to_dict(url).get("dbname", "").endswith("_test"):
        raise ValueError("test_database_required")
    policies, clauses = recommendation_explanation_data()
    with psycopg.connect(url) as connection:
        connection.execute("TRUNCATE public.f13_clauses, public.f13_policy_documents")
        for policy in policies:
            source = next(c for c in clauses if c.policy_id == policy.policy_id)
            connection.execute(
                """INSERT INTO public.f13_policy_documents
                   (policy_id, document_version, policy_name, category, source_url, checked_at,
                    publication_status, published_at) VALUES (%s,%s,%s,%s,%s,%s,'published',%s)""",
                (
                    policy.policy_id,
                    policy.document_version,
                    policy.policy_name,
                    policy.category,
                    source.source_url,
                    CHECKED_AT,
                    CHECKED_AT,
                ),
            )
        for clause in clauses:
            connection.execute(
                """INSERT INTO public.f13_clauses
                   (policy_id,document_version,clause_id,title,body,source_location,topics,
                    review_status,reviewed_by,reviewed_at,publication_status,published_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,'approved','fictional-test-reviewer',%s,
                           'published',%s)""",
                (
                    clause.policy_id,
                    clause.document_version,
                    clause.id,
                    clause.title,
                    clause.text,
                    f"가상 안내 / {clause.title}",
                    list(clause.topics),
                    CHECKED_AT,
                    CHECKED_AT,
                ),
            )


if __name__ == "__main__":
    database_url = test_database_url()
    migrate_test_database(database_url)
    seed_test_database(database_url)
    print("[가상] F13 테스트 DB의 문서 3개·조항 6개를 준비했습니다. 운영 게시가 아닙니다.")
