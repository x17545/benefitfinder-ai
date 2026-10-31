"""Internal extraction/review records, separate from published benefit contracts.

These records preserve complex clauses for people to review. They are not a rule
language, a database schema, or an API for the online calculation path.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from urllib.parse import urlsplit


@dataclass(frozen=True)
class PolicyReference:
    id: str
    domain: str
    categories: tuple[str, ...]


@dataclass(frozen=True)
class SourceDocument:
    id: str
    policy_id: str
    source_url: str
    document_version: str
    collected_at: datetime
    text: str
    official: bool = False

    def __post_init__(self) -> None:
        url = urlsplit(self.source_url)
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("document_url_invalid")
        if not all(
            value.strip()
            for value in (
                self.id,
                self.policy_id,
                self.document_version,
                self.text,
            )
        ):
            raise ValueError("document_fields_missing")
        if self.collected_at.utcoffset() is None:
            raise ValueError("document_time_requires_timezone")

    @property
    def content_sha256(self) -> str:
        """Hash the exact extracted text; do not normalize quoted clauses."""
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


class RuleKind(StrEnum):
    SIMULTANEOUS_EXCLUSION = "simultaneous_exclusion"
    CONDITIONAL = "conditional"
    HISTORY = "history"
    DEDUCTION = "deduction"
    CAP = "cap"
    UNKNOWN = "unknown"


class RuleDirection(StrEnum):
    BOTH = "both"
    SOURCE_TO_TARGET = "source_to_target"
    TARGET_TO_SOURCE = "target_to_source"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RuleCitation:
    document_id: str
    document_version: str
    content_sha256: str
    quote: str
    location: str


@dataclass(frozen=True)
class RuleCandidate:
    id: str
    source_policy_id: str
    target_policy_ids: tuple[str, ...]
    kind: RuleKind
    description: str
    citations: tuple[RuleCitation, ...]
    direction: RuleDirection = RuleDirection.UNKNOWN
    conditions: tuple[str, ...] = ()
    exceptions: tuple[str, ...] = ()
    period: str | None = None
    amount_effect: str | None = None
    unresolved: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReviewIssue:
    code: str
    document_id: str | None = None


@dataclass(frozen=True)
class RuleApproval:
    candidate_sha256: str
    documents_sha256: str
    reviewer: str
    reviewed_at: datetime


@dataclass(frozen=True)
class CandidateReview:
    candidate: RuleCandidate
    issues: tuple[ReviewIssue, ...]
    approval: RuleApproval | None = None

    @property
    def status(self) -> str:
        return "approved" if self.approval is not None else "needs_review"


class RuleExtractor(Protocol):
    def extract(self, documents: tuple[SourceDocument, ...]) -> tuple[RuleCandidate, ...]:
        """Read official documents only; never accept a user profile."""
        ...


@dataclass(frozen=True)
class RulePreparationResult:
    reviews: tuple[CandidateReview, ...]
    error_code: str | None = None
