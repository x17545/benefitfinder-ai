"""Offline review operations over complete, immutable local artifacts.

File I/O is supplied by the jobs assembly point. Every operation returns a new
snapshot; the caller keeps the previous artifact and never publishes these drafts.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from benefitfinder.application.policy_rules import (
    ReviewOperation,
    RuleCandidate,
    RuleReviewBatch,
    RuleReviewSnapshot,
    SourceDocument,
)
from benefitfinder.ingestion.policy_rule_review import (
    approve_candidate,
    prepare_rule_reviews,
    refresh_review,
)


class RuleReviewWorkflowError(ValueError):
    """A fixed error code with no imported text or provider details."""


@dataclass(frozen=True)
class _ImportedCandidates:
    candidates: tuple[RuleCandidate, ...]

    def extract(self, documents: tuple[SourceDocument, ...]) -> tuple[RuleCandidate, ...]:
        return self.candidates


def _check_time(
    at: datetime,
    batch: RuleReviewBatch,
    previous: RuleReviewSnapshot | None = None,
) -> None:
    if at.utcoffset() is None:
        raise RuleReviewWorkflowError("review_time_requires_timezone")
    if previous is not None and at < previous.created_at:
        raise RuleReviewWorkflowError("review_time_precedes_snapshot")
    if any(at < document.collected_at for document in batch.documents):
        raise RuleReviewWorkflowError("review_precedes_collection")


def prepare_snapshot(batch: RuleReviewBatch, *, at: datetime) -> RuleReviewSnapshot:
    """Import draft candidates without performing a model call or approving them."""
    result = prepare_rule_reviews(
        batch.documents,
        batch.policies,
        _ImportedCandidates(batch.candidates),
    )
    if result.error_code is not None:
        raise RuleReviewWorkflowError(result.error_code)
    _check_time(at, batch)
    return RuleReviewSnapshot(batch, result.reviews, at, ReviewOperation.PREPARED)


def inspect_snapshot(snapshot: RuleReviewSnapshot) -> RuleReviewSnapshot:
    """Recheck stored approvals before displaying or changing the artifact.

    This checks the embedded batch, not the live state of an official website.
    A fresh complete batch must be imported with refresh_snapshot after collection.
    """
    reviews = tuple(
        refresh_review(
            review,
            snapshot.batch.documents,
            snapshot.batch.policies,
            candidates=snapshot.batch.candidates,
        )
        for review in snapshot.reviews
    )
    return replace(snapshot, reviews=reviews)


def approve_snapshot(
    snapshot: RuleReviewSnapshot,
    *,
    candidate_id: str,
    reviewer: str,
    at: datetime,
) -> RuleReviewSnapshot:
    """Record a person's decision against the artifact's full current batch."""
    _check_time(at, snapshot.batch, snapshot)
    checked = inspect_snapshot(snapshot)
    matches = [
        index for index, review in enumerate(checked.reviews) if review.candidate.id == candidate_id
    ]
    if not matches:
        raise RuleReviewWorkflowError("review_candidate_not_found")
    if len(matches) != 1:
        raise RuleReviewWorkflowError("review_candidate_ambiguous")
    index = matches[0]
    try:
        approved = approve_candidate(
            checked.reviews[index],
            checked.batch.documents,
            checked.batch.policies,
            candidates=checked.batch.candidates,
            reviewer=reviewer,
            reviewed_at=at,
        )
    except ValueError as exc:
        # The underlying review functions emit only fixed codes.
        raise RuleReviewWorkflowError(str(exc)) from None
    reviews = list(checked.reviews)
    reviews[index] = approved
    return RuleReviewSnapshot(checked.batch, tuple(reviews), at, ReviewOperation.APPROVED)


def refresh_snapshot(
    snapshot: RuleReviewSnapshot,
    batch: RuleReviewBatch,
    *,
    at: datetime,
) -> RuleReviewSnapshot:
    """Invalidate old reviews against a fresh complete batch, retaining concerns.

    Missing documents/candidates are allowed here so their previous approvals can
    be revoked. This neither replaces review records nor imports new candidates
    as reviewed records. Re-extraction goes into a separately prepared artifact.
    """
    _check_time(at, batch, snapshot)
    refreshed = inspect_snapshot(replace(snapshot, batch=batch))
    return replace(refreshed, created_at=at, operation=ReviewOperation.REFRESHED)
