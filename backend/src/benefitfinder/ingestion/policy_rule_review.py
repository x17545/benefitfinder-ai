"""Validate extraction drafts and record explicit, version-bound human reviews.

No network, storage, publication, or policy calculation happens here. A matching
quote proves provenance, not the meaning or completeness of the extracted rule.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import asdict, replace
from datetime import datetime

from benefitfinder.application.policy_rules import (
    CandidateReview,
    PolicyReference,
    ReviewIssue,
    RuleApproval,
    RuleCandidate,
    RuleCitation,
    RuleDirection,
    RuleExtractor,
    RuleKind,
    RulePreparationResult,
    SourceDocument,
)
from benefitfinder.domains.youth import MVP_POLICY_CATEGORIES


def _in_scope(policy: PolicyReference) -> bool:
    return (
        policy.domain == "youth"
        and bool(policy.categories)
        and set(policy.categories) <= MVP_POLICY_CATEGORIES
    )


def _valid_inputs(
    documents: tuple[SourceDocument, ...],
    policies: tuple[PolicyReference, ...],
) -> bool:
    if not isinstance(documents, tuple) or not isinstance(policies, tuple):
        return False
    if not documents or not all(isinstance(item, SourceDocument) for item in documents):
        return False
    if not all(isinstance(item, PolicyReference) for item in policies):
        return False
    if not all(
        isinstance(item.id, str)
        and bool(item.id.strip())
        and isinstance(item.domain, str)
        and isinstance(item.categories, tuple)
        and all(isinstance(value, str) for value in item.categories)
        for item in policies
    ):
        return False
    if len({item.id for item in documents}) != len(documents):
        return False
    if len({item.id for item in policies}) != len(policies):
        return False
    catalog = {item.id: item for item in policies}
    return all(
        item.official is True and item.policy_id in catalog and _in_scope(catalog[item.policy_id])
        for item in documents
    )


def _candidate_shape_valid(candidate: object) -> bool:
    if not isinstance(candidate, RuleCandidate):
        return False
    if not all(
        isinstance(value, str)
        for value in (
            candidate.id,
            candidate.source_policy_id,
            candidate.description,
        )
    ):
        return False
    for values in (
        candidate.target_policy_ids,
        candidate.conditions,
        candidate.exceptions,
        candidate.unresolved,
    ):
        if not isinstance(values, tuple) or not all(isinstance(item, str) for item in values):
            return False
    if not isinstance(candidate.kind, RuleKind) or not isinstance(
        candidate.direction, RuleDirection
    ):
        return False
    if not all(
        value is None or isinstance(value, str)
        for value in (
            candidate.period,
            candidate.amount_effect,
        )
    ):
        return False
    return isinstance(candidate.citations, tuple) and all(
        isinstance(item, RuleCitation)
        and all(
            isinstance(value, str)
            for value in (
                item.document_id,
                item.document_version,
                item.content_sha256,
                item.quote,
                item.location,
            )
        )
        for item in candidate.citations
    )


def validate_candidate(
    candidate: RuleCandidate,
    documents: tuple[SourceDocument, ...],
    policies: tuple[PolicyReference, ...],
) -> tuple[ReviewIssue, ...]:
    """Return blockers; a clean result still requires a person's semantic review."""
    if not _candidate_shape_valid(candidate):
        return (ReviewIssue("invalid_candidate_shape"),)
    if not _valid_inputs(documents, policies):
        return (ReviewIssue("invalid_input"),)
    issues: list[ReviewIssue] = []
    catalog = {item.id: item for item in policies}
    sources = {item.id: item for item in documents}
    if not candidate.id.strip() or not candidate.description.strip():
        issues.append(ReviewIssue("missing_candidate_fields"))
    policy_ids = (candidate.source_policy_id, *candidate.target_policy_ids)
    if not candidate.target_policy_ids:
        issues.append(ReviewIssue("missing_target_policy"))
    if len(set(policy_ids)) != len(policy_ids):
        issues.append(ReviewIssue("duplicate_or_self_policy"))
    for policy_id in policy_ids:
        if policy_id not in catalog:
            issues.append(ReviewIssue("unknown_policy"))
        elif not _in_scope(catalog[policy_id]):
            issues.append(ReviewIssue("out_of_scope_policy"))

    # Complex clauses stay intact for future contract/support decisions. Never
    # convert them into an unconditional, permanent, symmetric exclusion pair.
    if (
        candidate.kind != RuleKind.SIMULTANEOUS_EXCLUSION
        or candidate.direction != RuleDirection.BOTH
        or candidate.conditions
        or candidate.exceptions
        or candidate.period is not None
        or candidate.amount_effect is not None
    ):
        issues.append(ReviewIssue("unsupported_rule"))
    if candidate.unresolved:
        issues.append(ReviewIssue("unresolved_details"))
    if not candidate.citations:
        issues.append(ReviewIssue("missing_evidence"))
    source_evidence = False
    for citation in candidate.citations:
        document = sources.get(citation.document_id)
        if document is None:
            issues.append(ReviewIssue("missing_document", citation.document_id))
            continue
        if document.policy_id not in policy_ids:
            issues.append(ReviewIssue("unrelated_document", document.id))
        if document.policy_id == candidate.source_policy_id:
            source_evidence = True
        if (
            citation.document_version != document.document_version
            or citation.content_sha256 != document.content_sha256
        ):
            issues.append(ReviewIssue("stale_evidence", document.id))
        if not citation.quote.strip() or citation.quote not in document.text:
            issues.append(ReviewIssue("quote_not_found", document.id))
        if not citation.location.strip():
            issues.append(ReviewIssue("missing_evidence_location", document.id))
    if not source_evidence:
        issues.append(ReviewIssue("missing_source_evidence"))
    return tuple(dict.fromkeys(issues))


def _candidate_sha256(candidate: RuleCandidate) -> str:
    payload = json.dumps(
        asdict(candidate), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _documents_sha256(documents: tuple[SourceDocument, ...]) -> str:
    versions = sorted(
        (
            item.id,
            item.policy_id,
            item.source_url,
            item.document_version,
            item.content_sha256,
        )
        for item in documents
    )
    payload = json.dumps(versions, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _batch_issues(candidates: tuple[RuleCandidate, ...]) -> tuple[tuple[ReviewIssue, ...], ...]:
    ids = Counter(item.id for item in candidates)
    pairs: dict[frozenset[str], set[int]] = {}
    for index, candidate in enumerate(candidates):
        for target in candidate.target_policy_ids:
            pair = frozenset((candidate.source_policy_id, target))
            pairs.setdefault(pair, set()).add(index)
    conflicting = set().union(*(indexes for indexes in pairs.values() if len(indexes) > 1))
    issues: list[tuple[ReviewIssue, ...]] = []
    for index, candidate in enumerate(candidates):
        blockers: list[ReviewIssue] = []
        if ids[candidate.id] > 1:
            blockers.append(ReviewIssue("duplicate_candidate_id"))
        if index in conflicting:
            blockers.append(ReviewIssue("conflicting_candidates"))
        issues.append(tuple(blockers))
    return tuple(issues)


def _current_batch_issues(
    candidate: RuleCandidate,
    candidates: tuple[RuleCandidate, ...],
) -> tuple[ReviewIssue, ...]:
    if not isinstance(candidates, tuple) or not all(
        _candidate_shape_valid(item) for item in candidates
    ):
        return (ReviewIssue("invalid_candidate_batch"),)
    if candidate not in candidates:
        return (ReviewIssue("candidate_not_in_batch"),)
    return _batch_issues(candidates)[candidates.index(candidate)]


def prepare_rule_reviews(
    documents: tuple[SourceDocument, ...],
    policies: tuple[PolicyReference, ...],
    extractor: RuleExtractor,
) -> RulePreparationResult:
    """Extract once through the injected interface; never approve a draft.

    An empty extraction does not prove coexistence. Errors carry fixed codes,
    with no provider message, document text, user profile, or credentials.
    """
    if not _valid_inputs(documents, policies):
        return RulePreparationResult((), "invalid_input")
    try:
        candidates = extractor.extract(documents)
    except Exception:
        return RulePreparationResult((), "extraction_failed")
    if not isinstance(candidates, tuple) or not all(
        _candidate_shape_valid(item) for item in candidates
    ):
        return RulePreparationResult((), "invalid_extraction_result")
    batch_issues = _batch_issues(candidates)
    reviews: list[CandidateReview] = []
    for index, candidate in enumerate(candidates):
        issues = list(validate_candidate(candidate, documents, policies))
        issues.extend(batch_issues[index])
        reviews.append(CandidateReview(candidate, tuple(issues)))
    return RulePreparationResult(tuple(reviews))


def approve_candidate(
    review: CandidateReview,
    documents: tuple[SourceDocument, ...],
    policies: tuple[PolicyReference, ...],
    *,
    candidates: tuple[RuleCandidate, ...],
    reviewer: str,
    reviewed_at: datetime,
) -> CandidateReview:
    """Record a person's explicit decision after reading the source and exceptions.

    This internal operation records review only. It neither publishes a rule nor
    confirms amounts, payment timing, policy eligibility, or extraction coverage.
    """
    if not reviewer.strip():
        raise ValueError("reviewer_required")
    if reviewed_at.utcoffset() is None:
        raise ValueError("review_time_requires_timezone")
    candidate = review.candidate
    issues = validate_candidate(candidate, documents, policies)
    if review.issues or issues or _current_batch_issues(candidate, candidates):
        raise ValueError("candidate_not_approvable")
    if any(reviewed_at < document.collected_at for document in documents):
        raise ValueError("review_precedes_collection")
    return CandidateReview(
        candidate,
        (),
        RuleApproval(
            _candidate_sha256(candidate),
            _documents_sha256(documents),
            reviewer.strip(),
            reviewed_at,
        ),
    )


def refresh_review(
    review: CandidateReview,
    documents: tuple[SourceDocument, ...],
    policies: tuple[PolicyReference, ...],
    *,
    candidates: tuple[RuleCandidate, ...],
) -> CandidateReview:
    """Revalidate before using a review; revoke approvals on evidence changes.

    Recollection of identical text/version does not advance the review timestamp.
    Existing review issues stay pending until a new batch is prepared and reviewed;
    refreshing evidence alone cannot resolve a person's outstanding concern.
    Missing sources fail closed. Extraction/collection failures must not overwrite
    stored successful records; persistence remains the caller's responsibility.
    """
    issues = list(validate_candidate(review.candidate, documents, policies))
    issues.extend(_current_batch_issues(review.candidate, candidates))
    issues.extend(review.issues)
    if review.approval is not None:
        if not _candidate_shape_valid(
            review.candidate
        ) or review.approval.candidate_sha256 != _candidate_sha256(review.candidate):
            issues.append(ReviewIssue("candidate_changed"))
        if not _valid_inputs(documents, policies) or (
            review.approval.documents_sha256 != _documents_sha256(documents)
        ):
            issues.append(ReviewIssue("documents_changed"))
    return replace(
        review,
        issues=tuple(dict.fromkeys(issues)),
        approval=(review.approval if not issues else None),
    )
