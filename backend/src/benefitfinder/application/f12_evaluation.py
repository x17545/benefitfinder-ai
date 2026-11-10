"""Deterministic, read-only coverage of human-approved F12 evidence, not approval."""

from __future__ import annotations

import re
from dataclasses import dataclass

from benefitfinder.application.policy_rules import RuleCitation, SourceDocument


@dataclass(frozen=True)
class EvaluationItem:
    id: str
    kind: str
    citations: tuple[RuleCitation, ...]
    target_policy_ids: tuple[str, ...] = ()
    rule_kind: str | None = None
    direction: str | None = None
    exceptions: tuple[str, ...] = ()
    amounts_won: tuple[int, ...] = ()


@dataclass(frozen=True)
class EvaluationCase:
    id: str
    category: str
    reference_document: SourceDocument
    candidate_document: SourceDocument | None
    references: tuple[EvaluationItem, ...]
    candidates: tuple[EvaluationItem, ...]
    extraction_error: str | None = None


_LINE = re.compile(r"(?:^|\b)(?:text )?lines? ([1-9][0-9]*)(?:-([1-9][0-9]*))?$")


def _words(text: str) -> str:
    return " ".join(text.split())


def _span(citation: RuleCitation, document: SourceDocument) -> tuple[tuple[int, int] | None, str | None]:
    if not document.official:
        return None, "source_not_official"
    if (citation.document_id, citation.document_version, citation.content_sha256) != (
        document.id, document.document_version, document.content_sha256
    ):
        return None, "citation_source_mismatch"
    if not citation.quote.strip() or citation.quote not in document.text:
        return None, "quote_not_in_source"
    match = _LINE.search(citation.location)
    if match is None:
        return None, "location_not_verifiable"
    first, last = int(match[1]), int(match[2] or match[1])
    lines = document.text.splitlines(keepends=True)
    if not 1 <= first <= last <= len(lines):
        return None, "location_out_of_range"
    window = "".join(lines[first - 1:last])
    quote = citation.quote.strip()
    offset = window.find(quote)
    if offset < 0:
        return None, "quote_location_mismatch"
    if offset != window.rfind(quote):
        return None, "ambiguous_quote_location"
    start = sum(len(line) for line in lines[:first - 1]) + offset
    return (start, start + len(quote)), None


def _covered(span: tuple[int, int], available: list[tuple[int, int]], text: str) -> int:
    start, end = span
    cursor, covered = start, 0
    for left, right in sorted(available):
        left, right = max(start, left, cursor), min(end, right)
        if right > left:
            # Separate, verbatim citations may omit only the whitespace between them.
            if text[cursor:left].isspace():
                covered += left - cursor
            covered += right - left
            cursor = right
    return covered


def _identity(item: EvaluationItem) -> tuple[object, ...]:
    if item.kind == "rule":
        return item.kind, tuple(sorted(item.target_policy_ids)), item.rule_kind, item.direction
    return (item.kind,)


def _source(document: SourceDocument) -> dict[str, str]:
    return {"id": document.id, "policy_id": document.policy_id, "url": document.source_url,
            "document_version": document.document_version, "content_sha256": document.content_sha256}


def _case(case: EvaluationCase) -> dict[str, object]:
    reference_spans: dict[str, list[tuple[int, int]]] = {}
    for item in case.references:
        spans = [_span(c, case.reference_document) for c in item.citations]
        if not spans or any(error for _, error in spans):
            raise ValueError("invalid_f12_evaluation_reference")
        reference_spans[item.id] = [span for span, _ in spans if span is not None]
        if item.kind == "rule" and any(
            not any(_words(e) in _words(c.quote) for c in item.citations) for e in item.exceptions
        ):
            raise ValueError("unsupported_f12_reference_exception")
    comparable = (case.extraction_error is None and case.candidate_document is not None
                  and _source(case.reference_document) == _source(case.candidate_document))
    candidates = []
    valid: dict[str, list[tuple[int, int]]] = {}
    valid_quotes: dict[str, list[str]] = {}
    for item in case.candidates:
        valid[item.id], valid_quotes[item.id] = [], []
        errors = []
        for index, citation in enumerate(item.citations, 1):
            span, error = _span(citation, case.candidate_document)
            if error:
                errors.append({"citation": index, "code": error})
            elif span is not None:
                valid[item.id].append(span)
                valid_quotes[item.id].append(citation.quote)
        if not item.citations:
            errors.append({"citation": None, "code": "citation_missing"})
        candidates.append({"id": item.id, "kind": item.kind,
                           "citation_count": len(item.citations),
                           "grounded_citations": len(valid[item.id]), "errors": errors})
    items = []
    exceptions = []
    scored_ids: set[str] = set()
    for reference in case.references:
        spans = reference_spans[reference.id]
        compatible = [c for c in case.candidates if _identity(c) == _identity(reference)]
        linked = [c for c in compatible if comparable and any(
            min(a[1], b[1]) > max(a[0], b[0]) for a in spans for b in valid[c.id]
        )]
        available = [s for c in linked for s in valid[c.id]]
        expected = sum(end - start for start, end in spans)
        count = sum(_covered(span, available, case.reference_document.text)
                    for span in spans) if comparable else 0
        status = ("not_comparable" if not comparable else "covered" if count == expected
                  else "partial" if count else "missing")
        amount_match = None
        if reference.kind == "amount" and status == "covered":
            amount_match = {value for c in linked for value in c.amounts_won} == set(
                reference.amounts_won
            )
        scored_ids.update(c.id for c in linked)
        items.append({"reference_id": reference.id, "kind": reference.kind, "status": status,
                      "expected_characters": expected,
                      "covered_characters": count if comparable else None,
                      "candidate_ids": [c.id for c in linked], "amount_values_match": amount_match})
        for index, expected_exception in enumerate(reference.exceptions, 1):
            exact = [c for c in linked if any(_words(e) == _words(expected_exception)
                                             for e in c.exceptions)]
            supported = any(_words(expected_exception) in _words(q)
                            for c in exact for q in valid_quotes[c.id])
            state = ("not_comparable" if not comparable else "matched" if supported
                     else "unsupported" if exact else "text_changed"
                     if any(c.exceptions for c in linked) else "missing")
            exceptions.append({"reference_id": reference.id, "exception": index, "status": state})
    # Partial gold cannot classify additional model output as false positives or approvals.
    for entry, item in zip(candidates, case.candidates, strict=True):
        approved_spans = [s for ref in case.references if _identity(ref) == _identity(item)
                          for s in reference_spans[ref.id]]
        entry["unscored_content"] = not comparable or any(
            _covered(s, approved_spans, case.candidate_document.text) < s[1] - s[0]
            for s in valid[item.id]
        ) or item.id not in scored_ids
    return {"id": case.id, "category": case.category, "comparable": comparable,
            "reference_source": _source(case.reference_document),
            "candidate_source": _source(case.candidate_document) if case.candidate_document else None,
            "extraction_error": case.extraction_error,
            "items": items, "exceptions": exceptions, "candidates": candidates}


def evaluate_cases(cases: tuple[EvaluationCase, ...]) -> dict[str, object]:
    """Evaluate coverage and grounded exception text; never change any review state."""
    if not cases or len({c.id for c in cases}) != len(cases):
        raise ValueError("invalid_f12_evaluation_cases")
    for case in cases:
        if (case.category not in {"employment", "housing"} or not case.references
                or len({i.id for i in case.references}) != len(case.references)
                or len({i.id for i in case.candidates}) != len(case.candidates)):
            raise ValueError("invalid_f12_evaluation_cases")
        if any(not i.id.strip() or i.kind not in {"rule", "eligibility", "amount", "timing"}
               for i in (*case.references, *case.candidates)):
            raise ValueError("invalid_f12_evaluation_cases")
        if (case.extraction_error is None and case.candidate_document is None) or (
            case.extraction_error is not None and (
                case.extraction_error not in {"context_limit", "model_failure", "extraction_failed"}
                or case.candidate_document is not None or case.candidates
            )
        ):
            raise ValueError("invalid_f12_evaluation_cases")
    results = [_case(case) for case in cases]
    items = [i for r in results for i in r["items"]]
    exceptions = [e for r in results for e in r["exceptions"]]
    candidates = [c for r in results for c in r["candidates"]]
    summary = {
        "cases": len(results), "comparable_cases": sum(r["comparable"] for r in results),
        "failed_extractions": sum(r["extraction_error"] is not None for r in results),
        "reference_items": len(items),
        "comparable_reference_items": sum(i["status"] != "not_comparable" for i in items),
        "covered_items": sum(i["status"] == "covered" for i in items),
        "partial_items": sum(i["status"] == "partial" for i in items),
        "missing_items": sum(i["status"] == "missing" for i in items),
        "not_comparable_items": sum(i["status"] == "not_comparable" for i in items),
        "amount_value_mismatches": sum(i["amount_values_match"] is False for i in items),
        "reference_exceptions": len(exceptions),
        "matched_exceptions": sum(e["status"] == "matched" for e in exceptions),
        "unmatched_exceptions": sum(e["status"] != "matched" for e in exceptions),
        "candidate_citations": sum(c["citation_count"] for c in candidates),
        "grounded_candidate_citations": sum(c["grounded_citations"] for c in candidates),
        "citation_errors": sum(len(c["errors"]) for c in candidates),
        "unscored_candidates": sum(c["unscored_content"] for c in candidates),
    }
    denominator = summary["comparable_reference_items"]
    summary["comparable_evidence_recall"] = (
        round(summary["covered_items"] / denominator, 4) if denominator else None
    )
    summary["evaluation_coverage"] = round(denominator / summary["reference_items"], 4)
    summary["approved_evidence_recall"] = (
        summary["comparable_evidence_recall"] if not summary["not_comparable_items"] else None
    )
    findings = any(summary[k] for k in (
        "partial_items", "missing_items", "not_comparable_items", "amount_value_mismatches",
        "unmatched_exceptions", "citation_errors",
    ))
    return {"format_version": 1, "kind": "f12_extraction_evaluation",
            "assessment": "findings" if findings else "no_findings",
            "scope": "approved_reference_only", "requires_human_review": True,
            "approval_effect": "none", "summary": summary, "cases": results,
            "limitations": [
                "승인한 근거의 범위만 평가하며 공고 전체의 누락이나 정밀도를 보장하지 않습니다.",
                "예외는 공백만 정규화한 텍스트와 해당 후보의 원문 인용을 대조합니다. 표현 변경은 사람 검토 대상입니다.",
                "원문 인용 일치는 정책 사실·판정·해석의 정확성이나 승인을 뜻하지 않습니다.",
                "승인 내보내기의 형식과 근거를 검사하지만 검수자 신원이나 승인 진위를 인증하지 않습니다.",
                "서로 다른 원문 버전은 비교 불가로 보고하며 누락률 분모에 섞지 않습니다.",
            ]}
