"""Explicit, source-bound publication of unreviewed catalog records."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import re

from benefitfinder.infrastructure.ai_catalog_review import _verify_real_source, project_youthcenter_source
from benefitfinder.infrastructure.policy_catalog_records import CatalogRecord, record_hash, validate_source_text
from benefitfinder.infrastructure.service_catalog_records import (
    ServiceCatalogRecord, service_record_hash, validate_service_sources,
)


def validate_unreviewed(record: CatalogRecord | ServiceCatalogRecord, source: str) -> str:
    if record.benefit.virtual_data or record.f12_review is not None:
        raise ValueError('invalid_unreviewed_catalog_record')
    if isinstance(record, ServiceCatalogRecord):
        if record.price is not None:
            raise ValueError('unreviewed_price_unsupported')
        validate_service_sources(record, source, None)
        return service_record_hash(record)
    if record.benefit.reviewed or not record.unresolved_combination_rules or record.payments is not None:
        raise ValueError('unreviewed_cash_cannot_be_calculated')
    validate_source_text(record, source)
    return record_hash(record)


def _verify_open_application(connection, record: CatalogRecord | ServiceCatalogRecord) -> None:
    if record.benefit.application.status != 'open':
        return
    binding = record.collection_sources[0]
    fields = connection.execute(
        'SELECT fields FROM public.collected_policies WHERE source=%s AND external_id=%s',
        (binding.source, binding.external_id),
    ).fetchone()[0]
    text = ' '.join(str(fields.get(k) or '') for k in ('plcySprtCn', 'plcyAplyMthdCn', 'etcMttrCn', 'srngMthdCn'))
    if (not str(fields.get('plcyAplyMthdCn') or '').strip() or
        re.search(r'별도\s*(?:공고|모집|신청)|예산\s*소진|조기\s*마감|(?:기관별|시설별|차수별|회차별)\s*(?:모집|공고)', text)):
        raise ValueError('unreviewed_open_application_unverified')
    period = fields.get('aplyPrdSeCd')
    today = datetime.now(timezone(timedelta(hours=9))).date()
    if period == '0057001':
        dates = re.findall(r'20\d{6}', str(fields.get('aplyYmd') or ''))
        if len(dates) < 2:
            raise ValueError('unreviewed_open_application_unverified')
        start, end = (datetime.strptime(d, '%Y%m%d').date() for d in (dates[0], dates[-1]))
        if not (start <= today <= end and record.benefit.application.starts_on == start.isoformat()
                and record.benefit.application.ends_on == end.isoformat()):
            raise ValueError('unreviewed_open_application_unverified')
    elif period != '0057002':
        raise ValueError('unreviewed_open_application_unverified')
    business_end = str(fields.get('bizPrdEndYmd') or '')
    if re.fullmatch(r'20\d{6}', business_end):
        end = datetime.strptime(business_end, '%Y%m%d').date()
        if end < today or not record.benefit.application.ends_on or datetime.fromisoformat(
            record.benefit.application.ends_on
        ).date() > end:
            raise ValueError('unreviewed_open_application_unverified')


def publish_unreviewed(database_url: str, entries: list[tuple[CatalogRecord | ServiceCatalogRecord, str]]) -> None:
    import psycopg
    from psycopg.types.json import Jsonb

    if not 1 <= len(entries) <= 50 or len({r.benefit.id for r, _ in entries}) != len(entries):
        raise ValueError('invalid_unreviewed_batch')
    digests = [validate_unreviewed(record, source) for record, source in entries]
    with psycopg.connect(database_url, connect_timeout=2) as connection:
        connection.execute("SET LOCAL statement_timeout = '5s'")
        connection.execute("SET LOCAL lock_timeout = '500ms'")
        if not connection.execute("SELECT 1 FROM public.benefitfinder_schema_migrations WHERE version='0014_unreviewed_catalog'").fetchone():
            raise ValueError('unreviewed_catalog_migration_required')
        for name in ('gov24', 'official_documents', 'youthcenter'):
            connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))', ('benefitfinder:collected_policy_generations:' + name,))
        for policy_id in sorted(record.benefit.id for record, _ in entries):
            connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))', (policy_id,))
        now = datetime.now(timezone.utc)
        for (record, source), digest in zip(entries, digests, strict=True):
            _verify_real_source(connection, record, source)
            _verify_open_application(connection, record)
            if connection.execute("SELECT 1 FROM public.policy_catalog_releases WHERE policy_id=%s AND publication_status='published'", (record.benefit.id,)).fetchone():
                raise ValueError('unreviewed_existing_release')
            provenance = {'document_version': record.benefit.source.document_version,
                          'source_sha256': record.source_sha256, 'record_sha256': digest}
            existing = connection.execute(
                'SELECT review_type,review_status,publication_status,approved_sha256,payload,unreviewed_source '
                'FROM public.policy_catalog_releases WHERE policy_id=%s AND document_version=%s',
                (record.benefit.id, record.benefit.source.document_version),
            ).fetchone()
            if existing is not None:
                expected = ('unreviewed', 'unreviewed', 'retired', digest,
                            record.model_dump(mode='json'), provenance)
                if existing != expected:
                    raise ValueError('unreviewed_retired_release_mismatch')
                connection.execute(
                    "UPDATE public.policy_catalog_releases SET publication_status='published',published_at=%s "
                    "WHERE policy_id=%s AND document_version=%s",
                    (now, record.benefit.id, record.benefit.source.document_version),
                )
                continue
            connection.execute("""INSERT INTO public.policy_catalog_releases
                (policy_id, document_version, payload, approved_sha256, review_status, reviewed_by,
                 reviewed_at, publication_status, published_at, review_type, unreviewed_source)
                VALUES (%s,%s,%s,%s,'unreviewed',NULL,NULL,'published',%s,'unreviewed',%s)""",
                (record.benefit.id, record.benefit.source.document_version,
                 Jsonb(record.model_dump(mode='json')), digest, now, Jsonb(provenance)))


def retire_unreviewed(database_url: str, entries: list[tuple[CatalogRecord | ServiceCatalogRecord, str]]) -> None:
    import psycopg

    if not 1 <= len(entries) <= 50 or len({r.benefit.id for r, _ in entries}) != len(entries):
        raise ValueError('invalid_unreviewed_batch')
    with psycopg.connect(database_url, connect_timeout=2) as connection:
        for name in ('gov24', 'official_documents', 'youthcenter'):
            connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))', ('benefitfinder:collected_policy_generations:' + name,))
        for record, source in sorted(entries, key=lambda e: e[0].benefit.id):
            connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))', (record.benefit.id,))
            digest = validate_unreviewed(record, source)
            result = connection.execute("UPDATE public.policy_catalog_releases SET publication_status='retired' WHERE policy_id=%s AND document_version=%s AND approved_sha256=%s AND review_type='unreviewed' AND publication_status='published'", (record.benefit.id, record.benefit.source.document_version, digest))
            if result.rowcount != 1:
                raise ValueError('unreviewed_release_not_current')



def retire_unreviewed_releases(database_url: str, releases: list[tuple[str, str, str]]) -> None:
    """Retire only exact current unreviewed releases by policy, version and approved hash.

    Unlike ``retire_unreviewed`` this needs no source text, so a release whose original
    source projection can no longer be rebuilt can still be retired. It changes a row
    only when all three values match a published unreviewed release, and the whole
    batch rolls back on any mismatch.
    """
    ids = [policy_id for policy_id, _, _ in releases]
    if (not 1 <= len(releases) <= 50 or len(set(ids)) != len(ids)
            or any(type(v) is not str or not v for item in releases for v in item)
            or any(re.fullmatch(r'[0-9a-f]{64}', digest) is None for _, _, digest in releases)):
        raise ValueError('invalid_unreviewed_release_batch')
    import psycopg

    with psycopg.connect(database_url, connect_timeout=2) as connection:
        connection.execute("SET LOCAL statement_timeout = '5s'")
        connection.execute("SET LOCAL lock_timeout = '500ms'")
        for name in ('gov24', 'official_documents', 'youthcenter'):
            connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))', ('benefitfinder:collected_policy_generations:' + name,))
        for policy_id, version, digest in sorted(releases):
            connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))', (policy_id,))
            result = connection.execute(
                "UPDATE public.policy_catalog_releases SET publication_status='retired' "
                "WHERE policy_id=%s AND document_version=%s AND approved_sha256=%s "
                "AND review_type='unreviewed' AND publication_status='published'",
                (policy_id, version, digest),
            )
            if result.rowcount != 1:
                raise ValueError('unreviewed_release_not_current')

_PUBLISHED_UNREVIEWED_SERVICES = """
    SELECT release.payload, release.approved_sha256, collected.fields,
           collected.material_sha256, collected.missing_detected_at
      FROM public.policy_catalog_releases AS release
      LEFT JOIN public.collected_policies AS collected
        ON collected.source = 'youthcenter'
       AND collected.external_id = release.payload->'collection_sources'->0->>'external_id'
     WHERE release.publication_status = 'published'
       AND release.review_type = 'unreviewed'
       AND release.payload->>'kind' = 'service'
     ORDER BY release.policy_id
"""


@dataclass(frozen=True)
class PublishedService:
    """One published unreviewed service and the current source projection of its policy.

    ``rebased`` is true when the projection differs from the one hashed at publication
    (for example after a projection format change) but still contains every exact
    citation of the record. Such a record can be retired only by its approved hash and
    must be re-hashed against ``source`` before a new version is published.
    """

    record: ServiceCatalogRecord
    source: str
    approved_sha256: str
    rebased: bool


def _citations(record: ServiceCatalogRecord) -> list:
    evidence = list(record.service_evidence)
    evidence += [item for clause in record.explanation_clauses or [] for item in [clause.evidence]]
    evidence += [item for condition in record.benefit.eligibility_conditions for item in condition.evidence]
    evidence += [item for conflict in record.benefit.conflicts for item in conflict.evidence]
    if record.benefit.application_guide is not None:
        evidence += record.benefit.application_guide.citations()
    return evidence


def load_published_unreviewed_services(
    connection,
) -> tuple[list[PublishedService], list[dict[str, str]]]:
    """Rebuild the published unreviewed services with their current source projection.

    Read-only. A record is returned only when its payload still hashes to the approved
    digest and its source is the bound, present generation. Anything else is returned
    as ``skipped`` with a reason instead of being guessed.
    """
    services: list[PublishedService] = []
    skipped: list[dict[str, str]] = []
    for payload, approved, fields, material, missing_at in connection.execute(
        _PUBLISHED_UNREVIEWED_SERVICES
    ).fetchall():
        record = ServiceCatalogRecord.model_validate(payload)
        bindings = record.collection_sources
        reason = None
        if service_record_hash(record) != approved:
            reason = 'approved_hash_mismatch'
        elif record.price is not None or record.benefit.virtual_data or record.f12_review is not None:
            reason = 'unsupported_unreviewed_service'
        elif len(bindings) != 1 or bindings[0].source != 'youthcenter':
            reason = 'unsupported_collection_source'
        elif fields is None:
            reason = 'collected_source_missing'
        elif missing_at is not None:
            reason = 'collected_source_marked_missing'
        elif material != bindings[0].material_sha256:
            reason = 'collected_source_changed'
        if reason is not None:
            skipped.append({'policy_id': record.benefit.id, 'reason': reason})
            continue
        source = project_youthcenter_source(fields)
        if hashlib.sha256(source.encode('utf-8')).hexdigest() == record.source_sha256:
            validate_unreviewed(record, source)
            services.append(PublishedService(record, source, approved, False))
        elif all(item.quote in source for item in _citations(record)):
            services.append(PublishedService(record, source, approved, True))
        else:
            skipped.append({'policy_id': record.benefit.id, 'reason': 'source_quotes_missing'})
    return services, skipped
