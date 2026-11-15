"""Verify an exact complete gov24 generation and all three preserved API bodies."""

from collections.abc import Callable
from datetime import datetime
import hashlib
from pathlib import PurePosixPath
import re

from benefitfinder.application.collected_policies import CollectedPolicyBatch
from benefitfinder.application.collection import CollectedRecord
from benefitfinder.ingestion.collected_generations import (
    CollectedGenerationError, _json, _timestamp,
)
from benefitfinder.ingestion.gov24 import (
    analysis_fields, gov24_hashed_fields, gov24_request, parse_gov24_response,
)
from benefitfinder.ingestion.versioning import record_sha256

ROOT = 'processed/f08-gov24'
RAW = 'raw/f08-gov24'
ENDPOINTS = ('serviceList', 'serviceDetail', 'supportConditions')
BASE_URL = 'https://api.odcloud.kr/api/gov24/v3/'


def _snapshot(read_file: Callable[[str, int], bytes], reference: object,
              source: str, endpoint: str, external_id: str) -> tuple[bytes, datetime]:
    if not isinstance(reference, str):
        raise ValueError
    path = PurePosixPath(reference)
    if (len(path.parts) != 3 or path.parts[:2] != (source, 'snapshots')
        or path.suffix != '.json' or '..' in path.parts):
        raise ValueError
    body = read_file(f'{RAW}/{path.as_posix()}', 16 * 1024 * 1024)
    meta = _json(read_file(f'{RAW}/{path.with_suffix(".meta.json").as_posix()}', 65_536))
    digest = hashlib.sha256(body).hexdigest()
    if (meta['source_name'] != source or meta['request_url'] != BASE_URL + endpoint
        or meta['request_params'] != dict(gov24_request(endpoint, external_id).params)
        or type(meta['status_code']) is not int or not 200 <= meta['status_code'] < 300
        or meta['content_sha256'] != digest or meta['body_file'] != path.name
        or not path.stem.endswith('-' + digest[:12])):
        raise ValueError
    return body, _timestamp(meta['fetched_at'])


def _load(read_file: Callable[[str, int], bytes]) -> CollectedPolicyBatch:
    scope = _json(read_file(f'{ROOT}/scope.json', 256 * 1024))
    ids = scope['external_ids']
    values = scope['category_values']
    if (set(scope) != {'external_ids', 'category_values'} or not isinstance(ids, list)
        or not 1 <= len(ids) <= 100 or any(type(key) is not str for key in ids)
        or len(set(ids)) != len(ids) or not isinstance(values, dict)
        or set(values) != {'employment', 'housing'}
        or any(not isinstance(v, list) or not v or any(type(s) is not str for s in v)
               for v in values.values())):
        raise ValueError
    categories = set(value for group in values.values() for value in group)
    status = _json(read_file(f'{ROOT}/status.json', 65_536))
    if type(status.get('format_version')) is not int or status['format_version'] != 1:
        raise ValueError
    success = status['last_success']
    if not isinstance(success, dict) or set(success) != {'run_id', 'completed_at', 'record_count'}:
        raise ValueError
    run_id, count = success['run_id'], success['record_count']
    if (type(run_id) is not str or re.fullmatch(r'[0-9a-f]{32}', run_id) is None
        or type(count) is not int or not 0 <= count <= len(ids)):
        raise ValueError
    completed_at = _timestamp(success['completed_at'])
    entries = _json(read_file(f'{ROOT}/runs/{run_id}/gov24/records.json', 64 * 1024 * 1024))
    records = []
    for external_id, entry in entries.items():
        if (external_id not in ids or not isinstance(entry, dict) or set(entry) != {
            'record_sha256', 'snapshot_ref', 'last_checked_at', 'missing_detected_at', 'fields'}):
            raise ValueError
        if entry['missing_detected_at'] is not None:
            if _timestamp(entry['missing_detected_at']) > completed_at:
                raise ValueError
            continue
        fields = entry['fields']
        if (not isinstance(fields, dict) or set(fields) != set(ENDPOINTS)
            or record_sha256(gov24_hashed_fields(fields)) != entry['record_sha256']):
            raise ValueError
        body, fetched_at = _snapshot(read_file, entry['snapshot_ref'], 'gov24',
                                     'serviceList', external_id)
        manifest = _json(body)
        if (set(manifest) != {'format_version', 'external_id', 'responses'}
            or type(manifest['format_version']) is not int or manifest['format_version'] != 1
            or manifest['external_id'] != external_id
            or not isinstance(manifest['responses'], dict)
            or set(manifest['responses']) != set(ENDPOINTS)
            or fetched_at != _timestamp(entry['last_checked_at']) or fetched_at > completed_at):
            raise ValueError
        for endpoint in ENDPOINTS:
            ref = manifest['responses'][endpoint]
            if not isinstance(ref, dict) or set(ref) != {'snapshot_ref', 'content_sha256'}:
                raise ValueError
            raw, observed_at = _snapshot(read_file, ref['snapshot_ref'], 'gov24-' + endpoint,
                                         endpoint, external_id)
            if hashlib.sha256(raw).hexdigest() != ref['content_sha256'] or observed_at > fetched_at:
                raise ValueError
            if parse_gov24_response(raw, external_id) != fields[endpoint]:
                raise ValueError
        projected = analysis_fields(fields)
        low, high = projected.get('ageStart'), projected.get('ageEnd')
        if (projected.get('serviceId') != external_id
            or projected.get('serviceCategory') not in categories
            or type(low) is not int or type(high) is not int
            or not 0 <= low <= high <= 120 or low > 39 or high < 19
            or fields['serviceList'].get('서비스명') != fields['serviceDetail'].get('서비스명')):
            raise ValueError
        records.append(CollectedRecord('gov24', external_id, entry['record_sha256'],
                                        entry['snapshot_ref'], fetched_at, fields))
    if len(records) != count:
        raise ValueError
    return CollectedPolicyBatch('gov24', run_id, completed_at, tuple(records))


def load_gov24_generation(read_file: Callable[[str, int], bytes]) -> CollectedPolicyBatch:
    """Integrity of local files does not establish human approval or authenticity."""
    try:
        return _load(read_file)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError, OverflowError):
        raise CollectedGenerationError('invalid_collected_gov24_generation') from None
