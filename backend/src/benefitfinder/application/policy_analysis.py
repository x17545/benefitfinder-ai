"""Offline F12 inputs and pending drafts. Never online calculation or approval."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
import re

from benefitfinder.application.policy_facts import PolicyFactsDraft
from benefitfinder.application.policy_rules import RuleCandidate, RuleCitation, SourceDocument


@dataclass(frozen=True)
class AnalysisPolicy:
    id: str
    name: str
    categories: tuple[str, ...]
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class AnalysisSource:
    source: str
    external_id: str
    material_sha256: str


@dataclass(frozen=True)
class PolicyAnalysisInput:
    policy_id: str
    documents: tuple[SourceDocument, ...]
    policies: tuple[AnalysisPolicy, ...]
    collection_sources: tuple[AnalysisSource, ...]

    def __post_init__(self) -> None:
        try:
            if not all(isinstance(items, tuple) for items in (
                self.documents, self.policies, self.collection_sources
            )):
                raise ValueError
            if not 1 <= len(self.documents) <= 4 or not 1 <= len(self.policies) <= 100:
                raise ValueError
            if not 1 <= len(self.collection_sources) <= 20:
                raise ValueError
            ids = set()
            names = []
            for policy in self.policies:
                if (re.fullmatch(r"youth:[A-Za-z0-9:_-]{1,100}", policy.id) is None
                    or policy.id in ids or not 1 <= len(policy.name.strip()) <= 120
                    or not policy.name.isprintable() or not policy.categories
                    or len(policy.categories) != len(set(policy.categories))
                    or not set(policy.categories) <= {"employment", "housing"}
                    or not isinstance(policy.categories, tuple) or not isinstance(policy.aliases, tuple)
                    or len(policy.aliases) > 8
                    or any(not 1 <= len(alias.strip()) <= 120 or not alias.isprintable()
                           for alias in policy.aliases)):
                    raise ValueError
                ids.add(policy.id)
                names.extend((policy.name, *policy.aliases))
            if self.policy_id not in ids or sum(map(len, names)) > 10_000:
                raise ValueError
            document_ids = set()
            for document in self.documents:
                if (document.policy_id != self.policy_id or not document.official
                    or document.id in document_ids or not 1 <= len(document.id) <= 120
                    or not document.id.isprintable() or len(document.document_version) > 120
                    or len(document.text) > 100_000 or "\0" in document.text):
                    raise ValueError
                document_ids.add(document.id)
            if sum(len(document.text) for document in self.documents) > 200_000:
                raise ValueError
            bindings = set()
            for source in self.collection_sources:
                key = (source.source, source.external_id)
                if (source.source != "youthcenter" or key in bindings
                    or not 1 <= len(source.external_id) <= 200 or not source.external_id.strip()
                    or "\0" in source.external_id
                    or re.fullmatch(r"[0-9a-f]{64}", source.material_sha256) is None):
                    raise ValueError
                bindings.add(key)
            self.fingerprint()
        except (AttributeError, TypeError, ValueError, RecursionError, OverflowError):
            raise ValueError("invalid_policy_analysis_input") from None

    def payload(self) -> dict[str, object]:
        result = asdict(self)
        for document, original in zip(result["documents"], self.documents, strict=True):
            document["collected_at"] = original.collected_at.isoformat()
        return result

    def fingerprint(self) -> str:
        encoded = json.dumps(self.payload(), ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode()
        if len(encoded) > 2 * 1024 * 1024:
            raise ValueError("invalid_policy_analysis_input")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class AnalysisEvidence:
    kind: str
    evidence: RuleCitation


@dataclass(frozen=True)
class AnalysisMissing:
    document_id: str
    field: str


@dataclass(frozen=True)
class AnalysisStep:
    tool: str
    outcome: str
    elapsed_ms: int


@dataclass(frozen=True)
class PolicyAnalysisResult:
    rules: tuple[RuleCandidate, ...]
    facts: tuple[PolicyFactsDraft, ...]
    additional: tuple[AnalysisEvidence, ...]
    missing: tuple[AnalysisMissing, ...]
    trace: tuple[AnalysisStep, ...]


@dataclass(frozen=True)
class AnalysisClaim:
    task_id: int
    token: str
    attempt: int
    input: PolicyAnalysisInput
    lease_until: datetime
    origin_recheck_id: int | None = None


class PolicyAnalysisStoreError(RuntimeError):
    """A fixed offline-store code, never input text or a driver exception."""
