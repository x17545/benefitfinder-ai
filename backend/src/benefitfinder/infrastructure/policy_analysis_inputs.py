"""Strict local manifest loading; no user profile or approval fields accepted."""

from datetime import datetime
import json
from pathlib import Path

from benefitfinder.application.policy_analysis import (
    AnalysisPolicy, AnalysisSource, PolicyAnalysisInput,
)
from benefitfinder.application.policy_rules import SourceDocument


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or any(type(item) is not str for item in value):
        raise ValueError
    return tuple(value)


def analysis_input_from_payload(raw: object) -> PolicyAnalysisInput:
    try:
        if not isinstance(raw, dict) or set(raw) != {
            'policy_id', 'documents', 'policies', 'collection_sources'
        } or type(raw['policy_id']) is not str:
            raise ValueError
        for key in ('documents', 'policies', 'collection_sources'):
            if not isinstance(raw[key], (list, tuple)):
                raise ValueError
        documents = []
        for row in raw['documents']:
            if (not isinstance(row, dict) or set(row) != {
                'id', 'policy_id', 'source_url', 'document_version', 'collected_at', 'text', 'official'
            } or row['official'] is not True
                or any(type(row[key]) is not str for key in row if key != 'official')):
                raise ValueError
            documents.append(SourceDocument(**{**row, 'collected_at': datetime.fromisoformat(
                row['collected_at'].replace('Z', '+00:00'))}))
        policies = []
        for row in raw['policies']:
            if (not isinstance(row, dict) or set(row) != {'id', 'name', 'categories', 'aliases'}
                or type(row['id']) is not str or type(row['name']) is not str):
                raise ValueError
            policies.append(AnalysisPolicy(row['id'], row['name'], _strings(row['categories']),
                                           _strings(row['aliases'])))
        sources = []
        for row in raw['collection_sources']:
            if (not isinstance(row, dict) or set(row) != {'source', 'external_id', 'material_sha256'}
                or any(type(value) is not str for value in row.values())):
                raise ValueError
            sources.append(AnalysisSource(**row))
        return PolicyAnalysisInput(raw['policy_id'], tuple(documents), tuple(policies), tuple(sources))
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError, OverflowError):
        raise ValueError('invalid_policy_analysis_input') from None


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('invalid_policy_analysis_input')
        result[key] = value
    return result


def load_policy_analysis_input(path: Path) -> PolicyAnalysisInput:
    try:
        with path.open('rb') as handle:
            encoded = handle.read(2 * 1024 * 1024 + 1)
        if len(encoded) > 2 * 1024 * 1024:
            raise ValueError
        raw = json.loads(encoded, object_pairs_hook=_unique,
                         parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(raw, dict) or type(raw.get('format_version')) is not int or raw.pop('format_version') != 1:
            raise ValueError
        return analysis_input_from_payload(raw)
    except (OSError, TypeError, ValueError, RecursionError):
        raise ValueError('invalid_policy_analysis_input') from None
