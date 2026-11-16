"""Native Decider protocol shared by offline local and hosted inference only."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Protocol

from benefitfinder.application.policy_rules import SourceDocument

MODEL_ID = "perplexity-ai/pplx-decider-flash-v1.3"
MODEL_REVISION = "d06fb3f2d97e0a52325a0d463c450cf0338f44fe"
MANIFEST_SHA256 = "8ca735506ab590ba73b86abc1dff5a8b3d3a9d39d48e3c8e4872dbfd7ec290b0"
# This pinned commit changed these two metadata files after the release manifest.
# Runtime code, adapters, tokenizer and weights still use the official manifest.
METADATA_FILES = {
    "README.md": {"bytes": 12031, "sha256": "c2f942dddef22da9a50429c03a3a4c2f36f14882e53aea20acd833796ab8daea"},
    "NOTICE": {"bytes": 132, "sha256": "7cb7e9b7b26e8dc334c64c5926de55e16c28ea181d4d10f02d58e9a6632bd3ed"},
}
QUESTION_VERSION = "youth-decider-v5-structured-first"
KINDS = ("amount", "duration", "eligibility", "application", "overlap", "exceptions", "timing")


class DeciderError(RuntimeError):
    """A fixed error code; provider exceptions and source text stay private."""


class Decisions(Protocol):
    @property
    def identity(self) -> dict[str, str]: ...
    def decide(self, requests: list[dict]) -> list[dict]: ...


@dataclass(frozen=True)
class LocatedSpan:
    kind: str
    start: int
    end: int
    text: str
    score: float
    method: str = 'locate'


@dataclass(frozen=True)
class SourceField:
    name: str
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class PreparedSource:
    spans: tuple[LocatedSpan, ...]
    structured: dict[str, dict]
    body: tuple[SourceField, ...]
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class DocumentExtraction:
    document: SourceDocument
    spans: tuple[LocatedSpan, ...]
    decisions: dict[str, object]
    raw_spans: tuple[LocatedSpan, ...] | None = None
    preparation: dict | None = None


@dataclass(frozen=True)
class Compilation:
    payload: dict | None
    reasons: tuple[str, ...]
    normalized: dict


def canonical_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()
