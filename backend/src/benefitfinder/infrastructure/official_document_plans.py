"""Bounded explicit mapping of confirmed official documents to MVP policies."""

import json
from pathlib import Path

from benefitfinder.application.official_documents import OfficialDocumentTarget


def official_document_plan_from_payload(raw: object) -> tuple[OfficialDocumentTarget, ...]:
    try:
        if (not isinstance(raw, dict) or set(raw) != {'format_version', 'documents'}
            or type(raw['format_version']) is not int or raw['format_version'] != 1
            or not isinstance(raw['documents'], list) or not 1 <= len(raw['documents']) <= 100):
            raise ValueError
        targets = []
        for row in raw['documents']:
            if (not isinstance(row, dict) or set(row) != {
                'external_id', 'policy_id', 'url', 'format', 'method', 'form',
                'categories', 'official_source_confirmed', 'required_text', 'content_id',
            } or row['official_source_confirmed'] is not True
                or not isinstance(row['form'], dict) or not isinstance(row['categories'], list)
                or not row['categories'] or not isinstance(row['required_text'], list)
                or not row['required_text']):
                raise ValueError
            targets.append(OfficialDocumentTarget(
                external_id=row['external_id'], policy_id=row['policy_id'], url=row['url'],
                format=row['format'], method=row['method'], form=tuple(sorted(row['form'].items())),
                categories=tuple(row['categories']), required_text=tuple(row['required_text']),
                content_id=row['content_id']))
        if len({target.external_id for target in targets}) != len(targets):
            raise ValueError
        return tuple(targets)
    except (TypeError, ValueError, KeyError, AttributeError, RecursionError):
        raise ValueError('invalid_official_document_plan') from None


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def load_official_document_plan(path: Path) -> tuple[OfficialDocumentTarget, ...]:
    try:
        with path.open('rb') as handle:
            encoded = handle.read(256 * 1024 + 1)
        if len(encoded) > 256 * 1024:
            raise ValueError
        return official_document_plan_from_payload(json.loads(encoded, object_pairs_hook=_unique,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError())))
    except (OSError, ValueError, TypeError, RecursionError):
        raise ValueError('invalid_official_document_plan') from None
