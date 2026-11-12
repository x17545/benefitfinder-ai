"""Prepare pending F12 inputs from explicitly mapped official API fields."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import hashlib
import re
from typing import Any, Protocol
from urllib.parse import urlencode

from benefitfinder.application.policy_analysis import (
    AnalysisPolicy, AnalysisSource, PolicyAnalysisInput,
)
from benefitfinder.application.policy_rules import SourceDocument


def safe_source_field(value: str) -> bool:
    return (type(value) is str and re.fullmatch(r'[A-Za-z][A-Za-z0-9]{0,63}', value) is not None
            and not any(part in value.lower() for part in (
                'key', 'token', 'secret', 'password', 'picnm', 'telno', 'email',
                'profile', 'parent', 'inqcnt',
            )))


@dataclass(frozen=True)
class ApiDocumentMapping:
    external_id: str
    name_field: str
    body_fields: tuple[str, ...]
    scope_guards: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if (type(self.external_id) is not str or not 1 <= len(self.external_id) <= 200
            or not self.external_id.strip() or not self.external_id.isprintable()
            or not safe_source_field(self.name_field)
            or not isinstance(self.body_fields, tuple) or not 1 <= len(self.body_fields) <= 40
            or any(not safe_source_field(key) for key in self.body_fields)
            or len(set(self.body_fields)) != len(self.body_fields)
            or self.name_field in self.body_fields
            or not isinstance(self.scope_guards, tuple) or not 1 <= len(self.scope_guards) <= 8):
            raise ValueError('invalid_f12_preparation_plan')
        keys = []
        for pair in self.scope_guards:
            if (not isinstance(pair, tuple) or len(pair) != 2 or not safe_source_field(pair[0])
                or type(pair[1]) is not str or not 1 <= len(pair[1]) <= 200
                or not pair[1].strip() or not pair[1].isprintable()):
                raise ValueError('invalid_f12_preparation_plan')
            keys.append(pair[0])
        if len(set(keys)) != len(keys):
            raise ValueError('invalid_f12_preparation_plan')


@dataclass(frozen=True)
class AnalysisPreparationTarget:
    policy_id: str
    documents: tuple[ApiDocumentMapping, ...]

    def __post_init__(self) -> None:
        if (type(self.policy_id) is not str
            or re.fullmatch(r'youth:[A-Za-z0-9:_-]{1,100}', self.policy_id) is None
            or not isinstance(self.documents, tuple) or not 1 <= len(self.documents) <= 4
            or any(not isinstance(item, ApiDocumentMapping) for item in self.documents)
            or len({item.external_id for item in self.documents}) != len(self.documents)):
            raise ValueError('invalid_f12_preparation_plan')


@dataclass(frozen=True)
class AnalysisPreparationPlan:
    policies: tuple[AnalysisPolicy, ...]
    targets: tuple[AnalysisPreparationTarget, ...]

    def __post_init__(self) -> None:
        if (not isinstance(self.policies, tuple) or not 1 <= len(self.policies) <= 100
            or not isinstance(self.targets, tuple) or not 1 <= len(self.targets) <= 100):
            raise ValueError('invalid_f12_preparation_plan')
        ids = set()
        names = []
        for item in self.policies:
            if (not isinstance(item, AnalysisPolicy) or type(item.id) is not str
                or re.fullmatch(r'youth:[A-Za-z0-9:_-]{1,100}', item.id) is None or item.id in ids
                or type(item.name) is not str or not 1 <= len(item.name.strip()) <= 120
                or not item.name.isprintable() or not isinstance(item.categories, tuple)
                or not 1 <= len(item.categories) <= 2
                or any(type(category) is not str for category in item.categories)
                or not set(item.categories) <= {'employment', 'housing'}
                or len(set(item.categories)) != len(item.categories)
                or not isinstance(item.aliases, tuple) or len(item.aliases) > 8
                or any(type(alias) is not str or not 1 <= len(alias.strip()) <= 120
                       or not alias.isprintable() for alias in item.aliases)):
                raise ValueError('invalid_f12_preparation_plan')
            ids.add(item.id)
            names.extend((item.name, *item.aliases))
        if (sum(map(len, names)) > 10_000
            or any(not isinstance(target, AnalysisPreparationTarget) or target.policy_id not in ids
                   for target in self.targets)
            or len({target.policy_id for target in self.targets}) != len(self.targets)):
            raise ValueError('invalid_f12_preparation_plan')


@dataclass(frozen=True)
class PreparationSource:
    external_id: str
    material_sha256: str
    # Time the complete generation first accepted this material (also reappearance).
    # An unchanged refresh must not manufacture a fresh document version/time.
    accepted_at: datetime
    fields: dict[str, Any]


class PreparationSources(Protocol):
    def load(self, external_ids: tuple[str, ...]) -> dict[str, PreparationSource]: ...


def _build_input(
    plan: AnalysisPreparationPlan, target: AnalysisPreparationTarget,
    sources: dict[str, PreparationSource], material_hash: Callable[[dict[str, Any]], str],
) -> PolicyAnalysisInput:
    policy = next(item for item in plan.policies if item.id == target.policy_id)
    documents = []
    bindings = []
    for mapping in target.documents:
        source = sources[mapping.external_id]
        if (source.external_id != mapping.external_id
            or source.accepted_at.utcoffset() is None
            or material_hash(source.fields) != source.material_sha256):
            raise ValueError('f12_preparation_source_invalid')
        if (source.fields.get(mapping.name_field) not in (policy.name, *policy.aliases)
            or any(source.fields.get(key) != value for key, value in mapping.scope_guards)):
            raise ValueError('f12_preparation_scope_changed')
        fragments = [f'[{mapping.name_field}]\n{source.fields[mapping.name_field]}']
        for key in mapping.body_fields:
            value = source.fields.get(key)
            if value is None or value == '':
                continue
            if type(value) not in (str, int):
                raise ValueError('f12_preparation_field_invalid')
            text = str(value)
            if '\0' in text:
                raise ValueError('f12_preparation_field_invalid')
            if text.strip():
                fragments.append(f'[{key}]\n{text}')
        if len(fragments) == 1:
            raise ValueError('f12_preparation_body_missing')
        text = '\n\n'.join(fragments)
        version = hashlib.sha256((source.material_sha256 + '\0' + text).encode()).hexdigest()
        documents.append(SourceDocument(
            'youthcenter:' + hashlib.sha256(mapping.external_id.encode()).hexdigest(),
            target.policy_id,
            'https://www.youthcenter.go.kr/go/ythip/getPlcy?' + urlencode({
                'plcyNo': mapping.external_id, 'rtnType': 'json',
            }),
            'api-v1:' + version, source.accepted_at, text, True,
        ))
        bindings.append(AnalysisSource('youthcenter', source.external_id, source.material_sha256))
    return PolicyAnalysisInput(target.policy_id, tuple(documents), plan.policies, tuple(bindings))


def prepare_analysis_inputs(
    plan: AnalysisPreparationPlan, sources: PreparationSources,
    register: Callable[[PolicyAnalysisInput], dict[str, Any]],
    material_hash: Callable[[dict[str, Any]], str],
) -> dict[str, int]:
    """Read one consistent source snapshot; failures never approve or publish data."""
    counts = {'targets': len(plan.targets), 'registered': 0, 'replayed': 0, 'failed': 0}
    ids = tuple(sorted({doc.external_id for target in plan.targets for doc in target.documents}))
    current = sources.load(ids)
    for target in plan.targets:
        try:
            result = register(_build_input(plan, target, current, material_hash))
            if type(result.get('replayed')) is not bool:
                raise ValueError('f12_preparation_registration_invalid')
            counts['replayed' if result['replayed'] else 'registered'] += 1
        except Exception:
            # A changed/missing/out-of-scope source needs a new confirmed mapping,
            # not a guessed policy, a truncated input or a public raw exception.
            counts['failed'] += 1
    return counts
