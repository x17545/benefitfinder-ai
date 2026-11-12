"""Read-only current policy releases and explicit, approval-bound publication."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from benefitfinder.application.published_policies import (
    PolicyCatalogUnavailable,
    PublishedPolicy,
)
from benefitfinder.infrastructure.policy_catalog_records import (
    CatalogRecord,
    record_hash,
    validate_source_text,
)

READ_SQL = """
SELECT payload, approved_sha256, published_at
FROM public.policy_catalog_releases
WHERE review_status = 'approved' AND publication_status = 'published'
  AND reviewed_by IS NOT NULL AND length(btrim(reviewed_by)) > 0
  AND reviewed_at IS NOT NULL AND published_at >= reviewed_at
  AND (payload ->> 'kind') IS DISTINCT FROM 'service'
"""


class PostgresPublishedPolicies:
    def __init__(self, database_url: str) -> None:
        try:
            if not database_url.strip() or not conninfo_to_dict(database_url).get("dbname"):
                raise ValueError
        except (psycopg.Error, ValueError):
            raise ValueError("invalid_policy_database_url") from None
        self._database_url = database_url

    def _read(self, policy_id: str | None = None) -> tuple[PublishedPolicy, ...]:
        try:
            with psycopg.connect(
                self._database_url,
                connect_timeout=2,
                options="-c default_transaction_read_only=on -c statement_timeout=2000 "
                "-c lock_timeout=500 -c timezone=UTC",
                application_name="benefitfinder-policy-catalog",
                row_factory=dict_row,
            ) as connection:
                sql = READ_SQL + (" AND policy_id = %s" if policy_id is not None else "")
                cursor = connection.execute(
                    sql + " ORDER BY policy_id", (policy_id,) if policy_id is not None else ()
                )
                # A safety limit fails the request; it never truncates the solver candidates.
                rows = cursor.fetchmany(5001)
                if len(rows) > 5000:
                    raise PolicyCatalogUnavailable("policy_catalog_size_limit")
            policies = []
            for row in rows:
                record = CatalogRecord.model_validate(row["payload"])
                if record_hash(record) != row["approved_sha256"]:
                    raise PolicyCatalogUnavailable("policy_catalog_approval_mismatch")
                policies.append(record.published(row["published_at"]))
            return tuple(policies)
        except (psycopg.Error, OSError, ValidationError, ValueError, TypeError, KeyError):
            raise PolicyCatalogUnavailable("policy_catalog_lookup_failed") from None

    def list_published(self) -> tuple[PublishedPolicy, ...]:
        return self._read()

    def get_published(self, policy_id: str) -> PublishedPolicy | None:
        rows = self._read(policy_id)
        return rows[0] if rows else None


def publish_policy(
    database_url: str,
    record: CatalogRecord,
    *,
    source_text: str,
    approved_sha256: str,
    reviewer: str,
    reviewed_at: datetime,
) -> None:
    """Only an explicit human-reviewed digest may replace the current version.

    This function does not create an approval. A changed record or source needs
    a new review. Errors roll back retirement and insertion together.
    """
    now = datetime.now(timezone.utc)
    if (
        not reviewer.strip()
        or reviewed_at.utcoffset() is None
        or reviewed_at > now
        or approved_sha256 != record_hash(record)
    ):
        raise ValueError("invalid_catalog_approval")
    validate_source_text(record, source_text)
    _publish_release(database_url, record.model_dump(mode='json'), approved_sha256=approved_sha256,
                     reviewer=reviewer, reviewed_at=reviewed_at)


def _publish_release(database_url: str, payload: dict[str, Any], *, approved_sha256: str,
                     reviewer: str, reviewed_at: datetime) -> None:
    """Persist an already source/approval-validated cash or service payload."""
    benefit = payload['benefit']
    checked_at = datetime.fromisoformat(benefit['source']['checked_at'].replace("Z", "+00:00"))
    if reviewed_at < checked_at:
        raise ValueError("approval_predates_source_check")
    with psycopg.connect(database_url, connect_timeout=2) as connection:
        connection.execute("SET LOCAL statement_timeout = '5s'")
        connection.execute("SET LOCAL lock_timeout = '500ms'")
        if payload.get('collection_sources'):
            if connection.execute(
                "SELECT 1 FROM public.benefitfinder_schema_migrations WHERE version = %s",
                ("0006_policy_source_rechecks",),
            ).fetchone() is None:
                raise ValueError("catalog_source_migration_required")
        # 0008's shared cash/service kind guard also uses this order for virtual
        # fixtures. Taking it before retirement avoids a policy/source inversion.
        for source in ('gov24', 'youthcenter'):
            connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                               ('benefitfinder:collected_policy_generations:' + source,))
        connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (benefit['id'],))
        published_at = datetime.now(timezone.utc)
        connection.execute(
            "UPDATE public.policy_catalog_releases SET publication_status='retired' "
            "WHERE policy_id=%s AND publication_status='published'",
            (benefit['id'],),
        )
        connection.execute(
            """INSERT INTO public.policy_catalog_releases
               (policy_id,document_version,payload,approved_sha256,review_status,
                reviewed_by,reviewed_at,publication_status,published_at)
               VALUES (%s,%s,%s,%s,'approved',%s,%s,'published',%s)""",
            (
                benefit['id'],
                benefit['source']['document_version'],
                Jsonb(payload),
                approved_sha256,
                reviewer.strip(),
                reviewed_at,
                published_at,
            ),
        )
