"""Re-derive a complete document generation from every preserved HTTP response."""

from collections.abc import Callable, Sequence
from pathlib import PurePosixPath
import hashlib
import re

from benefitfinder.application.collected_policies import CollectedPolicyBatch
from benefitfinder.application.collection import CollectedRecord, SourceResponse
from benefitfinder.application.official_documents import (
    MAX_DOCUMENT_BYTES, SOURCE_NAME, OfficialDocumentTarget,
)
from benefitfinder.ingestion.collected_generations import CollectedGenerationError, _json, _timestamp
from benefitfinder.ingestion.official_documents import document_fields, official_document_material_hash

ROOT = 'processed/f08-official-documents'
RAW = 'raw/f08-official-documents'


def _target(raw: object) -> OfficialDocumentTarget:
    if not isinstance(raw, dict) or set(raw) != {
        'external_id','policy_id','url','format','method','form','categories','required_text','content_id',
    } or any(not isinstance(raw[key],list) for key in ('form','categories','required_text')):
        raise ValueError
    return OfficialDocumentTarget(**{**raw, 'form':tuple(tuple(row) for row in raw['form']),
        'categories':tuple(raw['categories']), 'required_text':tuple(raw['required_text'])})


def load_official_document_generation(read_file: Callable[[str,int],bytes],
    pdf_pages: Callable[[bytes],Sequence[str]],
) -> CollectedPolicyBatch:
    try:
        scope = _json(read_file(f'{ROOT}/scope.json',256*1024))
        if set(scope) != {'documents'} or not isinstance(scope['documents'],list):
            raise ValueError
        identities = {row['external_id']:row for row in scope['documents']}
        if not 1 <= len(identities) == len(scope['documents']) <= 100:
            raise ValueError
        status = _json(read_file(f'{ROOT}/status.json',65_536))
        if type(status.get('format_version')) is not int or status['format_version'] != 1:
            raise ValueError
        success = status['last_success']
        if not isinstance(success,dict) or set(success) != {'run_id','completed_at','record_count'}:
            raise ValueError
        run_id = success['run_id']
        if (type(run_id) is not str or re.fullmatch(r'[0-9a-f]{32}',run_id) is None
            or type(success['record_count']) is not int):
            raise ValueError
        completed_at = _timestamp(success['completed_at'])
        run = f'{ROOT}/runs/{run_id}'
        manifest = _json(read_file(f'{run}/observations.json',1024*1024))
        if (set(manifest) != {'format_version','observations'}
            or type(manifest['format_version']) is not int or manifest['format_version'] != 1
            or not isinstance(manifest['observations'],list)
            or len(manifest['observations']) != len(identities)):
            raise ValueError
        entries = _json(read_file(f'{run}/{SOURCE_NAME}/records.json',64*1024*1024))
        records, seen, missing = [], set(), set()
        for observation in manifest['observations']:
            if not isinstance(observation,dict) or set(observation) != {'target','snapshot_ref'}:
                raise ValueError
            target = _target(observation['target'])
            identity = {'external_id':target.external_id,'policy_id':target.policy_id,
                        'categories':list(target.categories)}
            if target.external_id in seen or identities.get(target.external_id) != identity:
                raise ValueError
            seen.add(target.external_id)
            ref = observation['snapshot_ref']
            if type(ref) is not str:
                raise ValueError
            path = PurePosixPath(ref)
            source = SOURCE_NAME+'-'+target.method.lower()
            if (len(path.parts) != 3 or path.parts[:2] != (source,'snapshots')
                or path.suffix not in ('.body','.json') or '..' in path.parts):
                raise ValueError
            body = read_file(f'{RAW}/{ref}',MAX_DOCUMENT_BYTES)
            meta = _json(read_file(f'{RAW}/{path.with_suffix(".meta.json")}',65_536))
            digest = hashlib.sha256(body).hexdigest()
            at = _timestamp(meta['fetched_at'])
            if (meta['source_name'] != source or meta['request_url'] != target.url
                or meta['request_params'] != dict(target.form) or meta['content_sha256'] != digest
                or meta['body_file'] != path.name or not path.stem.endswith('-'+digest[:12])
                or type(meta['status_code']) is not int or at > completed_at):
                raise ValueError
            if meta['status_code'] in (404,410):
                missing.add(target.external_id)
                continue
            response = SourceResponse(meta['status_code'],body,meta['content_type'])
            fields = document_fields(target,response,pdf_pages)
            entry = entries[target.external_id]
            record_digest = official_document_material_hash(fields)
            if (set(entry) != {'record_sha256','snapshot_ref','last_checked_at',
                              'missing_detected_at','fields'}
                or entry['fields'] != fields or entry['record_sha256'] != record_digest
                or entry['snapshot_ref'] != ref or _timestamp(entry['last_checked_at']) != at
                or entry['missing_detected_at'] is not None):
                raise ValueError
            records.append(CollectedRecord(SOURCE_NAME,target.external_id,record_digest,ref,at,fields))
        for external_id,entry in entries.items():
            if external_id not in seen:
                raise ValueError
            if external_id in missing and (entry['missing_detected_at'] is None
                or _timestamp(entry['missing_detected_at']) > completed_at):
                raise ValueError
        if len(records) != success['record_count']:
            raise ValueError
        return CollectedPolicyBatch(SOURCE_NAME,run_id,completed_at,tuple(records))
    except (OSError,ValueError,TypeError,KeyError,AttributeError,RuntimeError,OverflowError):
        raise CollectedGenerationError('invalid_official_document_generation') from None
