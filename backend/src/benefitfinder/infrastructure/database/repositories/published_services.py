"""Source-bound service publication and validated read-only catalog access."""

from datetime import datetime, timezone

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from pydantic import ValidationError

from benefitfinder.application.published_policies import PolicyCatalogUnavailable
from benefitfinder.application.published_services import PublishedService
from benefitfinder.infrastructure.service_catalog_records import (
    ServiceCatalogRecord, service_record_hash, validate_service_sources,
)
from benefitfinder.infrastructure.database.repositories.published_policies import _publish_release


class PostgresPublishedServices:
    def __init__(self, database_url: str) -> None:
        try:
            if not database_url.strip() or not conninfo_to_dict(database_url).get('dbname'):
                raise ValueError
        except (psycopg.Error, ValueError):
            raise ValueError('invalid_service_database_url') from None
        self._database_url = database_url

    def _read(self, policy_id: str | None = None) -> tuple[PublishedService, ...]:
        try:
            with psycopg.connect(
                self._database_url, connect_timeout=2,
                options='-c default_transaction_read_only=on -c statement_timeout=2000 '
                        '-c lock_timeout=500 -c timezone=UTC',
                application_name='benefitfinder-services', row_factory=dict_row,
            ) as connection:
                query = """SELECT payload,approved_sha256,published_at,reviewed_at
                    FROM public.policy_catalog_releases
                    WHERE review_status='approved' AND publication_status='published'
                      AND reviewed_by IS NOT NULL AND length(btrim(reviewed_by))>0
                      AND reviewed_at IS NOT NULL AND published_at>=reviewed_at
                      AND payload->>'kind'='service'"""
                if policy_id is not None:
                    query += ' AND policy_id=%s'
                rows = connection.execute(query + ' ORDER BY policy_id',
                                          (policy_id,) if policy_id is not None else ()).fetchmany(5001)
            if len(rows) > 5000:
                raise PolicyCatalogUnavailable('service_catalog_size_limit')
            releases = []
            for row in rows:
                record = ServiceCatalogRecord.model_validate(row['payload'])
                if service_record_hash(record) != row['approved_sha256']:
                    raise PolicyCatalogUnavailable('service_catalog_approval_mismatch')
                if record.price is not None and datetime.fromisoformat(
                    record.price.source.checked_at.replace('Z', '+00:00')
                ) > row['reviewed_at']:
                    raise PolicyCatalogUnavailable('service_price_review_required')
                releases.append(record.published(row['published_at']))
            return tuple(releases)
        except (psycopg.Error, OSError, ValidationError, ValueError, TypeError, KeyError):
            raise PolicyCatalogUnavailable('service_catalog_lookup_failed') from None

    def list_published(self) -> tuple[PublishedService, ...]:
        return self._read()

    def get_published(self, policy_id: str) -> PublishedService | None:
        releases = self._read(policy_id)
        return releases[0] if releases else None


def publish_service(database_url: str, record: ServiceCatalogRecord, *, source_text: str,
                    price_source_text: str | None, approved_sha256: str, reviewer: str,
                    reviewed_at: datetime) -> None:
    if (not reviewer.strip() or reviewed_at.utcoffset() is None
        or reviewed_at > datetime.now(timezone.utc) or approved_sha256 != service_record_hash(record)):
        raise ValueError('invalid_service_approval')
    if record.price is not None and reviewed_at < datetime.fromisoformat(
        record.price.source.checked_at.replace('Z', '+00:00')
    ):
        raise ValueError('service_approval_predates_price')
    validate_service_sources(record, source_text, price_source_text)
    with psycopg.connect(database_url, connect_timeout=2,
                          options='-c statement_timeout=2000 -c lock_timeout=500') as connection:
        rows = connection.execute("""SELECT version FROM public.benefitfinder_schema_migrations
            WHERE version IN ('0006_policy_source_rechecks','0008_service_catalog')""").fetchall()
        if len(rows) != 2:
            raise ValueError('service_catalog_migration_required')
    _publish_release(database_url, record.model_dump(mode='json'), approved_sha256=approved_sha256,
                     reviewer=reviewer, reviewed_at=reviewed_at)
