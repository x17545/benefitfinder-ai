"""Bounded strict local configuration for already confirmed API mappings."""

import json
from pathlib import Path

from benefitfinder.application.policy_analysis import AnalysisPolicy
from benefitfinder.application.prepare_policy_analysis import (
    AnalysisPreparationPlan, AnalysisPreparationTarget, ApiDocumentMapping,
)


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def preparation_plan_from_payload(raw: object) -> AnalysisPreparationPlan:
    try:
        if (not isinstance(raw, dict) or set(raw) != {'format_version', 'policies', 'targets'}
            or type(raw['format_version']) is not int or raw['format_version'] != 1
            or not isinstance(raw['policies'], list) or not isinstance(raw['targets'], list)):
            raise ValueError
        policies = []
        for row in raw['policies']:
            if (not isinstance(row, dict) or set(row) != {'id', 'name', 'categories', 'aliases'}
                or not isinstance(row['categories'], list) or not isinstance(row['aliases'], list)):
                raise ValueError
            policies.append(AnalysisPolicy(row['id'], row['name'], tuple(row['categories']), tuple(row['aliases'])))
        targets = []
        for row in raw['targets']:
            if (not isinstance(row, dict) or set(row) != {'policy_id', 'documents'}
                or not isinstance(row['documents'], list)):
                raise ValueError
            documents = []
            for item in row['documents']:
                if (not isinstance(item, dict) or set(item) != {
                    'external_id', 'name_field', 'body_fields', 'scope_guards'
                } or not isinstance(item['body_fields'], list) or not isinstance(item['scope_guards'], dict)):
                    raise ValueError
                documents.append(ApiDocumentMapping(item['external_id'], item['name_field'],
                    tuple(item['body_fields']), tuple(sorted(item['scope_guards'].items()))))
            targets.append(AnalysisPreparationTarget(row['policy_id'], tuple(documents)))
        return AnalysisPreparationPlan(tuple(policies), tuple(targets))
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        raise ValueError('invalid_f12_preparation_plan') from None


def load_preparation_plan(path: Path) -> AnalysisPreparationPlan:
    try:
        with path.open('rb') as handle:
            encoded = handle.read(256 * 1024 + 1)
        if len(encoded) > 256 * 1024:
            raise ValueError
        return preparation_plan_from_payload(json.loads(encoded, object_pairs_hook=_unique,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError())))
    except (OSError, TypeError, ValueError, RecursionError):
        raise ValueError('invalid_f12_preparation_plan') from None
