"""Preserve confirmed HTML/PDF responses and derive inert source versions."""

from collections.abc import Callable, Sequence
from dataclasses import asdict
from datetime import datetime
import hashlib

from benefitfinder.application.collection import (
    CollectedRecord, RawSnapshot, RawSnapshotStore, RecordVersionStore,
    SourceRequest, SourceResponse,
)
from benefitfinder.application.official_documents import (
    MAX_DOCUMENT_BYTES, SOURCE_NAME, OfficialDocumentFetcher, OfficialDocumentTarget,
)
from benefitfinder.ingestion.collect import CollectionError, CollectionResult, _now
from benefitfinder.ingestion.notice_text import (
    NOTICE_TEXT_FORMAT, decode_html, html_to_text, pdf_pages_to_text, select_html_region,
)
from benefitfinder.ingestion.versioning import diff_records, record_sha256

PDF_TEXT_FORMAT = NOTICE_TEXT_FORMAT + '/pypdf-layout-v1'


def official_document_material_hash(fields: dict[str,object]) -> str:
    # Only a configured, uniquely found complete HTML element can exclude the
    # surrounding page (view counters, navigation). Its exact markup is hashed.
    material = dict(fields)
    if fields.get('format') == 'html' and fields.get('content_id') is not None:
        material.pop('raw_sha256')
    return record_sha256(material)


def document_fields(target: OfficialDocumentTarget, response: SourceResponse,
                    pdf_pages: Callable[[bytes], Sequence[str]]) -> dict[str, object]:
    """A failed PDF extraction still records changed raw bytes for invalidation."""
    if (type(response.status_code) is not int or response.status_code != 200
        or type(response.body) is not bytes or not 1 <= len(response.body) <= MAX_DOCUMENT_BYTES):
        raise ValueError('official_document_response_invalid')
    content_type = (response.content_type or '').split(';')[0].strip().lower()
    content_digest = hashlib.sha256(response.body).hexdigest()
    status = 'ready'
    if target.format == 'pdf':
        if not response.body.startswith(b'%PDF-'):
            raise ValueError('official_document_response_invalid')
        try:
            text = pdf_pages_to_text(pdf_pages(response.body))
        except ValueError:
            text, status = None, 'requires_review'
    else:
        if content_type not in ('text/html', 'application/xhtml+xml'):
            raise ValueError('official_document_response_invalid')
        html = decode_html(response.body,response.content_type)
        if target.content_id is not None:
            html = select_html_region(html,target.content_id)
        content_digest = hashlib.sha256(html.encode()).hexdigest()
        text = html_to_text(html)
    if text is not None and (len(text) > 100_000 or '\0' in text
                            or any(anchor not in text for anchor in target.required_text)):
        if target.format != 'pdf':
            raise ValueError('official_document_identity_unconfirmed')
        text, status = None, 'requires_review'
    return {'policy_id':target.policy_id, 'categories':list(target.categories),
            'source_url':target.url, 'format':target.format,
            'request_method':target.method, 'request_form':dict(target.form),
            'raw_sha256':hashlib.sha256(response.body).hexdigest(),
            'content_id':target.content_id,'content_sha256':content_digest,
            'text_format':PDF_TEXT_FORMAT if target.format == 'pdf' else NOTICE_TEXT_FORMAT,
            'text_status':status, 'text':text,
            'text_sha256':hashlib.sha256(text.encode()).hexdigest() if text is not None else None}


def collect_official_documents(targets: tuple[OfficialDocumentTarget, ...],
    fetcher: OfficialDocumentFetcher, snapshots: RawSnapshotStore, versions: RecordVersionStore,
    clock: Callable[[], datetime], pdf_pages: Callable[[bytes], Sequence[str]],
    save_manifest: Callable[[dict[str, object]], None],
) -> CollectionResult:
    started = _now(clock)
    records, observations = [], []
    try:
        if not 1 <= len(targets) <= 100 or len({t.external_id for t in targets}) != len(targets):
            raise ValueError('invalid_official_document_targets')
        for target in targets:
            response = fetcher.fetch(target)
            fetched_at = _now(clock)
            if (type(response.status_code) is not int or type(response.body) is not bytes
                or len(response.body) > MAX_DOCUMENT_BYTES):
                raise ValueError('official_document_response_invalid')
            digest = hashlib.sha256(response.body).hexdigest()
            snapshot = RawSnapshot(SOURCE_NAME + '-' + target.method.lower(),
                SourceRequest(target.url,dict(target.form)),fetched_at,response.status_code,
                response.content_type,digest,response.body)
            ref = snapshots.save(snapshot)
            observations.append({'target':asdict(target), 'snapshot_ref':ref})
            if response.status_code in (404,410):
                continue
            fields = document_fields(target,response,pdf_pages)
            records.append(CollectedRecord(SOURCE_NAME,target.external_id,official_document_material_hash(fields),
                ref,fetched_at,fields))
        changes = diff_records(SOURCE_NAME,versions.latest_hashes(SOURCE_NAME),records,complete=True)
        save_manifest({'format_version':1,'observations':observations})
        versions.save_versions(records,changes,checked_at=_now(clock))
        return CollectionResult(SOURCE_NAME,started,tuple(records),tuple(changes),(),True)
    except Exception:
        # No partial generation or raw exception is promoted to the last success.
        return CollectionResult(SOURCE_NAME,started,(),(),(
            CollectionError(0,'official_document_collection_failed','official_document_collection_failed'),),False)
