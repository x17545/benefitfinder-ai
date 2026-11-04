"""Local create-only storage for internal rule-review working files.

Internal local working format (version 1). Not a shared API or
publication contract. Reads strictly and writes atomically without
ever overwriting an existing result.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import NoReturn

from benefitfinder.application.policy_rules import CandidateReview
from benefitfinder.application.policy_rules import PolicyReference
from benefitfinder.application.policy_rules import ReviewIssue
from benefitfinder.application.policy_rules import ReviewOperation
from benefitfinder.application.policy_rules import RuleApproval
from benefitfinder.application.policy_rules import RuleCandidate
from benefitfinder.application.policy_rules import RuleCitation
from benefitfinder.application.policy_rules import RuleDirection
from benefitfinder.application.policy_rules import RuleKind
from benefitfinder.application.policy_rules import RuleReviewBatch
from benefitfinder.application.policy_rules import RuleReviewSnapshot
from benefitfinder.application.policy_rules import SourceDocument

FORMAT_VERSION: int = 1
INVALID_RULE_REVIEW_FILE: str = "invalid_rule_review_file"
UNSUPPORTED_RULE_REVIEW_FILE_VERSION: str = "unsupported_rule_review_file_version"
RULE_REVIEW_OUTPUT_EXISTS: str = "rule_review_output_exists"
RULE_REVIEW_IO_ERROR: str = "rule_review_io_error"

_HEX_DIGITS: frozenset[str] = frozenset("0123456789abcdefABCDEF")


class RuleReviewFileError(ValueError):
    """Fixed-code failure for local rule-review file handling."""


def _fail_invalid() -> NoReturn:
    raise RuleReviewFileError(INVALID_RULE_REVIEW_FILE) from None


def _fail_version() -> NoReturn:
    raise RuleReviewFileError(UNSUPPORTED_RULE_REVIEW_FILE_VERSION) from None


def _fail_exists() -> NoReturn:
    raise RuleReviewFileError(RULE_REVIEW_OUTPUT_EXISTS) from None


def _fail_io() -> NoReturn:
    raise RuleReviewFileError(RULE_REVIEW_IO_ERROR) from None


def _assert_utf8(value: str) -> None:
    try:
        value.encode("utf-8")
    except (UnicodeEncodeError, ValueError):
        raise RuleReviewFileError(INVALID_RULE_REVIEW_FILE) from None


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    seen: dict[str, object] = {}
    for key, value in pairs:
        if key in seen:
            raise RuleReviewFileError(INVALID_RULE_REVIEW_FILE) from None
        seen[key] = value
    return seen


def _reject_constant(value: str) -> None:
    raise RuleReviewFileError(INVALID_RULE_REVIEW_FILE) from None


def _read_json_object(path: Path) -> dict[str, object]:
    target = Path(path)
    try:
        raw = target.read_bytes()
    except OSError:
        _fail_io()
    try:
        decoded = raw.decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        _fail_invalid()
    try:
        data = json.loads(
            decoded,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except RuleReviewFileError:
        raise
    except Exception:
        _fail_invalid()
    if not isinstance(data, dict):
        _fail_invalid()
    return data


def _check_only_allowed(data: dict[str, object], allowed: set[str]) -> None:
    for key in data:
        if key not in allowed:
            _fail_invalid()


def _require_present(data: dict[str, object], key: str) -> object:
    if key not in data:
        _fail_invalid()
    return data[key]


def _require_str(data: dict[str, object], key: str) -> str:
    value = _require_present(data, key)
    if not isinstance(value, str):
        _fail_invalid()
    _assert_utf8(value)
    return value


def _require_str_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        _fail_invalid()
    for item in value:
        if not isinstance(item, str):
            _fail_invalid()
        _assert_utf8(item)
    result: tuple[str, ...] = tuple(value)
    return result


def _check_format_version(data: dict[str, object]) -> None:
    value = _require_present(data, "format_version")
    if type(value) is not int:
        _fail_invalid()
    if value != FORMAT_VERSION:
        _fail_version()


def _parse_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        _fail_invalid()
    _assert_utf8(value)
    text_value: str = value
    if text_value.endswith("Z"):
        text_value = text_value[:-1] + "+00:00"
    elif text_value.endswith("z"):
        text_value = text_value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text_value)
    except Exception:
        _fail_invalid()
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail_invalid()
    return parsed


def _parse_sha256(value: object) -> str:
    if not isinstance(value, str):
        _fail_invalid()
    _assert_utf8(value)
    digest: str = value
    if len(digest) != 64:
        _fail_invalid()
    for char in digest:
        if char not in _HEX_DIGITS:
            _fail_invalid()
    return digest


def _parse_reviewer(value: object) -> str:
    if not isinstance(value, str):
        _fail_invalid()
    _assert_utf8(value)
    reviewer: str = value.strip()
    _assert_utf8(reviewer)
    if not reviewer:
        _fail_invalid()
    return reviewer


def _parse_policy(data: object) -> PolicyReference:
    if not isinstance(data, dict):
        _fail_invalid()
    policy_data: dict[str, object] = data
    _check_only_allowed(policy_data, {"id", "domain", "categories"})
    policy_id = _require_str(policy_data, "id")
    domain = _require_str(policy_data, "domain")
    raw_categories = _require_present(policy_data, "categories")
    categories = _require_str_list(raw_categories)
    try:
        return PolicyReference(id=policy_id, domain=domain, categories=categories)
    except Exception:
        _fail_invalid()


def _parse_document(data: object) -> SourceDocument:
    if not isinstance(data, dict):
        _fail_invalid()
    doc_data: dict[str, object] = data
    _check_only_allowed(
        doc_data,
        {
            "id",
            "policy_id",
            "source_url",
            "document_version",
            "collected_at",
            "text",
            "official",
        },
    )
    doc_id = _require_str(doc_data, "id")
    policy_id = _require_str(doc_data, "policy_id")
    source_url = _require_str(doc_data, "source_url")
    document_version = _require_str(doc_data, "document_version")
    raw_collected = _require_present(doc_data, "collected_at")
    collected_at = _parse_datetime(raw_collected)
    text_content = _require_str(doc_data, "text")
    if "official" in doc_data:
        raw_official = doc_data["official"]
        if not isinstance(raw_official, bool):
            _fail_invalid()
        official: bool = raw_official
    else:
        official = False
    try:
        return SourceDocument(
            id=doc_id,
            policy_id=policy_id,
            source_url=source_url,
            document_version=document_version,
            collected_at=collected_at,
            text=text_content,
            official=official,
        )
    except Exception:
        _fail_invalid()


def _parse_citation(data: object) -> RuleCitation:
    if not isinstance(data, dict):
        _fail_invalid()
    cite_data: dict[str, object] = data
    _check_only_allowed(
        cite_data,
        {"document_id", "document_version", "content_sha256", "quote", "location"},
    )
    document_id = _require_str(cite_data, "document_id")
    document_version = _require_str(cite_data, "document_version")
    raw_digest = _require_present(cite_data, "content_sha256")
    content_sha256 = _parse_sha256(raw_digest)
    quote = _require_str(cite_data, "quote")
    location = _require_str(cite_data, "location")
    try:
        return RuleCitation(
            document_id=document_id,
            document_version=document_version,
            content_sha256=content_sha256,
            quote=quote,
            location=location,
        )
    except Exception:
        _fail_invalid()


def _parse_kind(value: object) -> RuleKind:
    if not isinstance(value, str):
        _fail_invalid()
    _assert_utf8(value)
    try:
        return RuleKind(value)
    except Exception:
        _fail_invalid()


def _parse_direction(value: object) -> RuleDirection:
    if not isinstance(value, str):
        _fail_invalid()
    _assert_utf8(value)
    try:
        return RuleDirection(value)
    except Exception:
        _fail_invalid()


def _parse_optional_str(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        _fail_invalid()
    _assert_utf8(value)
    return value


def _parse_candidate(data: object) -> RuleCandidate:
    if not isinstance(data, dict):
        _fail_invalid()
    cand_data: dict[str, object] = data
    _check_only_allowed(
        cand_data,
        {
            "id",
            "source_policy_id",
            "target_policy_ids",
            "kind",
            "description",
            "citations",
            "direction",
            "conditions",
            "exceptions",
            "period",
            "amount_effect",
            "unresolved",
        },
    )
    cand_id = _require_str(cand_data, "id")
    source_policy_id = _require_str(cand_data, "source_policy_id")
    raw_targets = _require_present(cand_data, "target_policy_ids")
    target_policy_ids = _require_str_list(raw_targets)
    raw_kind = _require_present(cand_data, "kind")
    kind = _parse_kind(raw_kind)
    description = _require_str(cand_data, "description")
    raw_citations = _require_present(cand_data, "citations")
    if not isinstance(raw_citations, list):
        _fail_invalid()
    citations = tuple(_parse_citation(item) for item in raw_citations)
    if "direction" in cand_data:
        direction = _parse_direction(cand_data["direction"])
    else:
        direction = RuleDirection.UNKNOWN
    if "conditions" in cand_data:
        conditions = _require_str_list(cand_data["conditions"])
    else:
        conditions = ()
    if "exceptions" in cand_data:
        exceptions = _require_str_list(cand_data["exceptions"])
    else:
        exceptions = ()
    if "period" in cand_data:
        period = _parse_optional_str(cand_data["period"])
    else:
        period = None
    if "amount_effect" in cand_data:
        amount_effect = _parse_optional_str(cand_data["amount_effect"])
    else:
        amount_effect = None
    if "unresolved" in cand_data:
        unresolved = _require_str_list(cand_data["unresolved"])
    else:
        unresolved = ()
    try:
        return RuleCandidate(
            id=cand_id,
            source_policy_id=source_policy_id,
            target_policy_ids=target_policy_ids,
            kind=kind,
            description=description,
            citations=citations,
            direction=direction,
            conditions=conditions,
            exceptions=exceptions,
            period=period,
            amount_effect=amount_effect,
            unresolved=unresolved,
        )
    except Exception:
        _fail_invalid()


def _parse_issue(data: object) -> ReviewIssue:
    if not isinstance(data, dict):
        _fail_invalid()
    issue_data: dict[str, object] = data
    _check_only_allowed(issue_data, {"code", "document_id"})
    raw_code = _require_present(issue_data, "code")
    if not isinstance(raw_code, str) or not raw_code.strip():
        _fail_invalid()
    _assert_utf8(raw_code)
    code: str = raw_code
    if "document_id" not in issue_data or issue_data["document_id"] is None:
        document_id: str | None = None
    else:
        raw_doc = issue_data["document_id"]
        if not isinstance(raw_doc, str):
            _fail_invalid()
        _assert_utf8(raw_doc)
        document_id = raw_doc
    try:
        return ReviewIssue(code=code, document_id=document_id)
    except Exception:
        _fail_invalid()


def _parse_approval(data: object) -> RuleApproval | None:
    if data is None:
        return None
    if not isinstance(data, dict):
        _fail_invalid()
    approval_data: dict[str, object] = data
    _check_only_allowed(
        approval_data,
        {"candidate_sha256", "documents_sha256", "reviewer", "reviewed_at"},
    )
    raw_candidate = _require_present(approval_data, "candidate_sha256")
    candidate_sha256 = _parse_sha256(raw_candidate)
    raw_documents = _require_present(approval_data, "documents_sha256")
    documents_sha256 = _parse_sha256(raw_documents)
    raw_reviewer = _require_present(approval_data, "reviewer")
    reviewer = _parse_reviewer(raw_reviewer)
    raw_reviewed = _require_present(approval_data, "reviewed_at")
    reviewed_at = _parse_datetime(raw_reviewed)
    try:
        return RuleApproval(
            candidate_sha256=candidate_sha256,
            documents_sha256=documents_sha256,
            reviewer=reviewer,
            reviewed_at=reviewed_at,
        )
    except Exception:
        _fail_invalid()


def _parse_review(data: object) -> CandidateReview:
    if not isinstance(data, dict):
        _fail_invalid()
    review_data: dict[str, object] = data
    _check_only_allowed(review_data, {"candidate", "issues", "approval"})
    raw_candidate = _require_present(review_data, "candidate")
    candidate = _parse_candidate(raw_candidate)
    raw_issues = _require_present(review_data, "issues")
    if not isinstance(raw_issues, list):
        _fail_invalid()
    issues = tuple(_parse_issue(item) for item in raw_issues)
    if "approval" not in review_data:
        _fail_invalid()
    approval = _parse_approval(review_data["approval"])
    try:
        return CandidateReview(candidate=candidate, issues=issues, approval=approval)
    except Exception:
        _fail_invalid()


def _parse_batch_dict(data: dict[str, object]) -> RuleReviewBatch:
    _check_only_allowed(data, {"format_version", "documents", "policies", "candidates"})
    _check_format_version(data)
    raw_documents = _require_present(data, "documents")
    raw_policies = _require_present(data, "policies")
    raw_candidates = _require_present(data, "candidates")
    if not isinstance(raw_documents, list):
        _fail_invalid()
    if not isinstance(raw_policies, list):
        _fail_invalid()
    if not isinstance(raw_candidates, list):
        _fail_invalid()
    documents = tuple(_parse_document(item) for item in raw_documents)
    policies = tuple(_parse_policy(item) for item in raw_policies)
    candidates = tuple(_parse_candidate(item) for item in raw_candidates)
    try:
        return RuleReviewBatch(documents=documents, policies=policies, candidates=candidates)
    except Exception:
        _fail_invalid()


def _parse_operation(value: object) -> ReviewOperation:
    if not isinstance(value, str):
        _fail_invalid()
    _assert_utf8(value)
    try:
        return ReviewOperation(value)
    except Exception:
        _fail_invalid()


def _parse_snapshot_dict(data: dict[str, object]) -> RuleReviewSnapshot:
    _check_only_allowed(data, {"format_version", "operation", "created_at", "batch", "reviews"})
    _check_format_version(data)
    raw_operation = _require_present(data, "operation")
    operation = _parse_operation(raw_operation)
    raw_created = _require_present(data, "created_at")
    created_at = _parse_datetime(raw_created)
    raw_batch = _require_present(data, "batch")
    if not isinstance(raw_batch, dict):
        _fail_invalid()
    batch = _parse_batch_dict(raw_batch)
    raw_reviews = _require_present(data, "reviews")
    if not isinstance(raw_reviews, list):
        _fail_invalid()
    reviews = tuple(_parse_review(item) for item in raw_reviews)
    try:
        return RuleReviewSnapshot(
            batch=batch, reviews=reviews, created_at=created_at, operation=operation
        )
    except Exception:
        _fail_invalid()


def _to_iso(value: object) -> str:
    if not isinstance(value, datetime):
        _fail_invalid()
    moment: datetime = value
    if moment.tzinfo is None or moment.utcoffset() is None:
        _fail_invalid()
    return moment.isoformat()


def _document_to_json(document: SourceDocument) -> dict[str, object]:
    return {
        "id": document.id,
        "policy_id": document.policy_id,
        "source_url": document.source_url,
        "document_version": document.document_version,
        "collected_at": _to_iso(document.collected_at),
        "text": document.text,
        "official": document.official,
    }


def _policy_to_json(policy: PolicyReference) -> dict[str, object]:
    return {
        "id": policy.id,
        "domain": policy.domain,
        "categories": list(policy.categories),
    }


def _citation_to_json(citation: RuleCitation) -> dict[str, object]:
    return {
        "document_id": citation.document_id,
        "document_version": citation.document_version,
        "content_sha256": citation.content_sha256,
        "quote": citation.quote,
        "location": citation.location,
    }


def _candidate_to_json(candidate: RuleCandidate) -> dict[str, object]:
    return {
        "id": candidate.id,
        "source_policy_id": candidate.source_policy_id,
        "target_policy_ids": list(candidate.target_policy_ids),
        "kind": candidate.kind.value,
        "description": candidate.description,
        "citations": [_citation_to_json(item) for item in candidate.citations],
        "direction": candidate.direction.value,
        "conditions": list(candidate.conditions),
        "exceptions": list(candidate.exceptions),
        "period": candidate.period,
        "amount_effect": candidate.amount_effect,
        "unresolved": list(candidate.unresolved),
    }


def _issue_to_json(issue: ReviewIssue) -> dict[str, object]:
    return {"code": issue.code, "document_id": issue.document_id}


def _approval_to_json(approval: RuleApproval) -> dict[str, object]:
    return {
        "candidate_sha256": approval.candidate_sha256,
        "documents_sha256": approval.documents_sha256,
        "reviewer": approval.reviewer,
        "reviewed_at": _to_iso(approval.reviewed_at),
    }


def _review_to_json(review: CandidateReview) -> dict[str, object]:
    if review.approval is None:
        approval_json: dict[str, object] | None = None
    else:
        approval_json = _approval_to_json(review.approval)
    return {
        "candidate": _candidate_to_json(review.candidate),
        "issues": [_issue_to_json(item) for item in review.issues],
        "approval": approval_json,
    }


def _batch_to_json(batch: RuleReviewBatch) -> dict[str, object]:
    if not isinstance(batch, RuleReviewBatch):
        _fail_invalid()
    if not isinstance(batch.documents, tuple):
        _fail_invalid()
    if not isinstance(batch.policies, tuple):
        _fail_invalid()
    if not isinstance(batch.candidates, tuple):
        _fail_invalid()
    return {
        "format_version": FORMAT_VERSION,
        "documents": [_document_to_json(item) for item in batch.documents],
        "policies": [_policy_to_json(item) for item in batch.policies],
        "candidates": [_candidate_to_json(item) for item in batch.candidates],
    }


def _snapshot_to_json(snapshot: RuleReviewSnapshot) -> dict[str, object]:
    if not isinstance(snapshot, RuleReviewSnapshot):
        _fail_invalid()
    if not isinstance(snapshot.batch, RuleReviewBatch):
        _fail_invalid()
    if not isinstance(snapshot.reviews, tuple):
        _fail_invalid()
    if not isinstance(snapshot.operation, ReviewOperation):
        _fail_invalid()
    return {
        "format_version": FORMAT_VERSION,
        "operation": snapshot.operation.value,
        "created_at": _to_iso(snapshot.created_at),
        "batch": _batch_to_json(snapshot.batch),
        "reviews": [_review_to_json(item) for item in snapshot.reviews],
    }


def _dump_payload(payload: dict[str, object]) -> bytes:
    try:
        text_payload = (
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        )
    except Exception:
        _fail_invalid()
    try:
        return text_payload.encode("utf-8")
    except Exception:
        _fail_invalid()


def _write_create_only(target: Path, payload: dict[str, object]) -> None:
    normalized = Path(target)
    parent = normalized.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        _fail_io()
    try:
        if os.path.lexists(normalized):
            _fail_exists()
    except RuleReviewFileError:
        raise
    except OSError:
        _fail_io()
    encoded = _dump_payload(payload)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=str(parent),
            prefix=".rule-review-",
            suffix=".tmp",
            delete=False,
        ) as tmp_file:
            tmp_path = Path(tmp_file.name)
            try:
                os.chmod(tmp_path, 0o600)
            except OSError:
                _fail_io()
            tmp_file.write(encoded)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        if tmp_path is None:
            _fail_io()
        try:
            os.link(tmp_path, normalized)
        except FileExistsError:
            _fail_exists()
        except OSError:
            _fail_io()
    except RuleReviewFileError:
        raise
    except OSError:
        _fail_io()
    except Exception:
        _fail_io()
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass


def load_rule_batch(path: Path) -> RuleReviewBatch:
    """Read a version 1 batch file strictly into domain records."""
    try:
        data = _read_json_object(Path(path))
    except RuleReviewFileError:
        raise
    except Exception:
        _fail_invalid()
    try:
        return _parse_batch_dict(data)
    except RuleReviewFileError:
        raise
    except Exception:
        _fail_invalid()


def load_rule_snapshot(path: Path) -> RuleReviewSnapshot:
    """Read a version 1 snapshot file strictly into domain records."""
    try:
        data = _read_json_object(Path(path))
    except RuleReviewFileError:
        raise
    except Exception:
        _fail_invalid()
    try:
        return _parse_snapshot_dict(data)
    except RuleReviewFileError:
        raise
    except Exception:
        _fail_invalid()


def write_rule_batch(path: Path, batch: RuleReviewBatch) -> None:
    """Publish a batch file once without overwriting existing results."""
    if not isinstance(batch, RuleReviewBatch):
        _fail_invalid()
    payload = _batch_to_json(batch)
    _write_create_only(Path(path), payload)


def write_rule_snapshot(path: Path, snapshot: RuleReviewSnapshot) -> None:
    """Publish a snapshot file once without overwriting existing results."""
    if not isinstance(snapshot, RuleReviewSnapshot):
        _fail_invalid()
    payload = _snapshot_to_json(snapshot)
    _write_create_only(Path(path), payload)
