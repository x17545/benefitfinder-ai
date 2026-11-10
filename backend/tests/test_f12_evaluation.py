"""F12 extraction evaluation regressions with synthetic documents.

Only virtual SourceDocument/RuleCitation fixtures are used. No real source
text, network, DB, .env, or data/local access.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from benefitfinder.application.f12_evaluation import (
    EvaluationCase,
    EvaluationItem,
    evaluate_cases,
)
from benefitfinder.application.policy_rules import RuleCitation, SourceDocument

UTC = timezone.utc
COLLECTED = datetime(2026, 10, 20, 0, 0, tzinfo=UTC)

LINE1 = "청년 취업 지원금은 월 50만원이다."
LINE2 = "중복 수혜는 불가하며 예외는 별도 표기한다."
LINE3 = "주거 지원은 보증금 대출 1억원을 제공한다."
LINE4 = "신청은 온라인으로 하며 마감은 12월이다."
BASE_TEXT = LINE1 + "\n" + LINE2 + "\n" + LINE3 + "\n" + LINE4 + "\n"

RULE_KIND = "simultaneous_exclusion"
BOTH = "both"


def make_document(
    doc_id: str = "doc-virtual-01",
    version: str = "v2026-01",
    text: str = BASE_TEXT,
    official: bool = True,
) -> SourceDocument:
    return SourceDocument(
        id=doc_id,
        policy_id="youth-employment-virtual-01",
        source_url="https://example.invalid/docs/" + doc_id,
        document_version=version,
        collected_at=COLLECTED,
        text=text,
        official=official,
    )


def cite(document: SourceDocument, quote: str, location: str) -> RuleCitation:
    return RuleCitation(
        document_id=document.id,
        document_version=document.document_version,
        content_sha256=document.content_sha256,
        quote=quote,
        location=location,
    )


def make_item(
    item_id: str,
    kind: str,
    citations: tuple[RuleCitation, ...],
    target_policy_ids: tuple[str, ...] = (),
    rule_kind: str | None = None,
    direction: str | None = None,
    exceptions: tuple[str, ...] = (),
    amounts_won: tuple[int, ...] = (),
) -> EvaluationItem:
    return EvaluationItem(
        id=item_id,
        kind=kind,
        citations=citations,
        target_policy_ids=target_policy_ids,
        rule_kind=rule_kind,
        direction=direction,
        exceptions=exceptions,
        amounts_won=amounts_won,
    )


def make_case(
    case_id: str,
    ref_doc: SourceDocument,
    cand_doc: SourceDocument,
    refs: tuple[EvaluationItem, ...],
    cands: tuple[EvaluationItem, ...],
    category: str = "employment",
) -> EvaluationCase:
    return EvaluationCase(
        id=case_id,
        category=category,
        reference_document=ref_doc,
        candidate_document=cand_doc,
        references=refs,
        candidates=cands,
    )


def first_case(result: dict[str, object]) -> dict[str, object]:
    cases = result["cases"]
    assert isinstance(cases, list)
    first = cases[0]
    assert isinstance(first, dict)
    return first


class TestF12Evaluation(unittest.TestCase):
    def test_span_match_ignores_ids_and_order(self) -> None:
        doc = make_document()
        refs = (
            make_item("ref-first", "eligibility", (cite(doc, LINE1, "line 1"),)),
            make_item("ref-second", "eligibility", (cite(doc, LINE3, "line 3"),)),
        )
        cands = (
            make_item("cand-second", "eligibility", (cite(doc, LINE3, "line 3"),)),
            make_item("cand-first", "eligibility", (cite(doc, LINE1, "line 1"),)),
        )
        result = evaluate_cases((make_case("case-order", doc, doc, refs, cands),))
        case = first_case(result)
        self.assertTrue(case["comparable"])
        items = case["items"]
        assert isinstance(items, list)
        self.assertEqual(items[0]["status"], "covered")
        self.assertEqual(items[0]["candidate_ids"], ["cand-first"])
        self.assertEqual(items[1]["status"], "covered")
        self.assertEqual(items[1]["candidate_ids"], ["cand-second"])
        summary = result["summary"]
        assert isinstance(summary, dict)
        self.assertEqual(summary["covered_items"], 2)
        self.assertEqual(summary["approved_evidence_recall"], 1.0)

    def test_union_coverage_and_partial(self) -> None:
        doc = make_document()
        refs = (
            make_item(
                "ref-union",
                "eligibility",
                (cite(doc, LINE1, "line 1"), cite(doc, LINE3, "line 3")),
            ),
            make_item(
                "ref-partial",
                "eligibility",
                (cite(doc, LINE2, "line 2"), cite(doc, LINE4, "line 4")),
            ),
        )
        cands = (
            make_item("cand-a1", "eligibility", (cite(doc, LINE1, "line 1"),)),
            make_item("cand-a2", "eligibility", (cite(doc, LINE3, "line 3"),)),
            make_item("cand-b1", "eligibility", (cite(doc, LINE2, "line 2"),)),
        )
        result = evaluate_cases((make_case("case-union", doc, doc, refs, cands),))
        items = first_case(result)["items"]
        assert isinstance(items, list)
        self.assertEqual(items[0]["status"], "covered")
        self.assertEqual(items[0]["covered_characters"], len(LINE1) + len(LINE3))
        self.assertEqual(set(items[0]["candidate_ids"]), {"cand-a1", "cand-a2"})
        self.assertEqual(items[1]["status"], "partial")
        self.assertEqual(items[1]["covered_characters"], len(LINE2))
        self.assertEqual(items[1]["expected_characters"], len(LINE2) + len(LINE4))
        self.assertEqual(items[1]["candidate_ids"], ["cand-b1"])

    def test_adjacent_verbatim_lines_can_join_but_missing_words_cannot(self) -> None:
        doc = make_document()
        reference = make_item("ref-lines", "eligibility", (
            cite(doc, LINE1 + "\n" + LINE2, "lines 1-2"),
        ))
        first = make_item("first", "eligibility", (cite(doc, LINE1, "line 1"),))
        for quote, expected in ((LINE2, "covered"), (LINE2[3:], "partial")):
            with self.subTest(quote=quote):
                second = make_item("second", "eligibility", (cite(doc, quote, "line 2"),))
                result = evaluate_cases((make_case("lines", doc, doc, (reference,), (first, second)),))
                self.assertEqual(first_case(result)["items"][0]["status"], expected)

    def test_version_change_is_not_comparable(self) -> None:
        ref_doc = make_document(doc_id="doc-version", version="v1")
        cand_doc = make_document(doc_id="doc-version", version="v2")
        refs = (make_item("ref-1", "eligibility", (cite(ref_doc, LINE1, "line 1"),)),)
        cands = (make_item("cand-1", "eligibility", (cite(cand_doc, LINE1, "line 1"),)),)
        result = evaluate_cases((make_case("case-version", ref_doc, cand_doc, refs, cands),))
        case = first_case(result)
        self.assertFalse(case["comparable"])
        items = case["items"]
        assert isinstance(items, list)
        self.assertEqual(items[0]["status"], "not_comparable")
        self.assertIsNone(items[0]["covered_characters"])
        self.assertEqual(items[0]["candidate_ids"], [])
        summary = result["summary"]
        assert isinstance(summary, dict)
        self.assertEqual(summary["comparable_reference_items"], 0)
        self.assertIsNone(summary["approved_evidence_recall"])
        self.assertEqual(summary["not_comparable_items"], 1)
        candidates = case["candidates"]
        assert isinstance(candidates, list)
        self.assertEqual(candidates[0]["grounded_citations"], 1)
        self.assertTrue(candidates[0]["unscored_content"])

    def test_exceptions_require_separate_field(self) -> None:
        doc = make_document()
        refs = (
            make_item(
                "ref-miss",
                "rule",
                (cite(doc, LINE2, "line 2"),),
                target_policy_ids=("p-a",),
                rule_kind=RULE_KIND,
                direction=BOTH,
                exceptions=("중복 수혜는 불가",),
            ),
            make_item(
                "ref-change",
                "rule",
                (cite(doc, LINE2, "line 2"),),
                target_policy_ids=("p-b",),
                rule_kind=RULE_KIND,
                direction=BOTH,
                exceptions=("예외는 별도 표기",),
            ),
            make_item(
                "ref-unsup",
                "rule",
                (cite(doc, LINE3, "line 3"),),
                target_policy_ids=("p-c",),
                rule_kind=RULE_KIND,
                direction=BOTH,
                exceptions=("보증금 대출",),
            ),
        )
        cands = (
            make_item(
                "cand-miss",
                "rule",
                (cite(doc, LINE2, "line 2"),),
                target_policy_ids=("p-a",),
                rule_kind=RULE_KIND,
                direction=BOTH,
                exceptions=(),
            ),
            make_item(
                "cand-change",
                "rule",
                (cite(doc, LINE2, "line 2"),),
                target_policy_ids=("p-b",),
                rule_kind=RULE_KIND,
                direction=BOTH,
                exceptions=("전혀 다른 예외 문구",),
            ),
            make_item(
                "cand-unsup",
                "rule",
                (cite(doc, "주거 지원은", "line 3"),),
                target_policy_ids=("p-c",),
                rule_kind=RULE_KIND,
                direction=BOTH,
                exceptions=("보증금 대출",),
            ),
        )
        result = evaluate_cases((make_case("case-exc", doc, doc, refs, cands),))
        case = first_case(result)
        exceptions = case["exceptions"]
        assert isinstance(exceptions, list)
        by_ref = {entry["reference_id"]: entry["status"] for entry in exceptions}
        self.assertEqual(by_ref["ref-miss"], "missing")
        self.assertEqual(by_ref["ref-change"], "text_changed")
        self.assertEqual(by_ref["ref-unsup"], "unsupported")

    def test_invalid_candidate_citations_are_reported(self) -> None:
        doc = make_document()
        refs = (make_item("ref-1", "eligibility", (cite(doc, LINE1, "line 1"),)),)
        bad_hash = RuleCitation(
            document_id=doc.id,
            document_version=doc.document_version,
            content_sha256="0" * 64,
            quote=LINE1,
            location="line 1",
        )
        cands = (
            make_item(
                "cand-bad-quote",
                "eligibility",
                (cite(doc, "가상 존재하지 않는 문구 999", "line 1"),),
            ),
            make_item(
                "cand-bad-location",
                "eligibility",
                (cite(doc, LINE1, "page 1"),),
            ),
            make_item("cand-bad-hash", "eligibility", (bad_hash,)),
            make_item(
                "cand-bad-place",
                "eligibility",
                (cite(doc, LINE3, "line 1"),),
            ),
        )
        result = evaluate_cases((make_case("case-invalid", doc, doc, refs, cands),))
        case = first_case(result)
        candidates = case["candidates"]
        assert isinstance(candidates, list)
        codes = [entry["errors"][0]["code"] for entry in candidates]
        self.assertEqual(
            codes,
            [
                "quote_not_in_source",
                "location_not_verifiable",
                "citation_source_mismatch",
                "quote_location_mismatch",
            ],
        )
        items = case["items"]
        assert isinstance(items, list)
        self.assertEqual(items[0]["status"], "missing")
        summary = result["summary"]
        assert isinstance(summary, dict)
        self.assertEqual(summary["citation_errors"], 4)
        bad_ref = make_item(
            "ref-bad", "eligibility", (cite(doc, "가상 존재하지 않는 문구 999", "line 1"),)
        )
        with self.assertRaisesRegex(ValueError, "invalid_f12_evaluation_reference"):
            evaluate_cases((make_case("case-bad-ref", doc, doc, (bad_ref,), cands[:1]),))

    def test_different_target_or_direction_does_not_match(self) -> None:
        doc = make_document()
        refs = (
            make_item(
                "ref-rule",
                "rule",
                (cite(doc, LINE2, "line 2"),),
                target_policy_ids=("p-a",),
                rule_kind=RULE_KIND,
                direction=BOTH,
            ),
        )
        cands = (
            make_item(
                "cand-other-target",
                "rule",
                (cite(doc, LINE2, "line 2"),),
                target_policy_ids=("p-b",),
                rule_kind=RULE_KIND,
                direction=BOTH,
            ),
            make_item(
                "cand-other-direction",
                "rule",
                (cite(doc, LINE2, "line 2"),),
                target_policy_ids=("p-a",),
                rule_kind=RULE_KIND,
                direction="source_to_target",
            ),
        )
        result = evaluate_cases((make_case("case-identity", doc, doc, refs, cands),))
        case = first_case(result)
        items = case["items"]
        assert isinstance(items, list)
        self.assertEqual(items[0]["status"], "missing")
        self.assertEqual(items[0]["candidate_ids"], [])
        candidates = case["candidates"]
        assert isinstance(candidates, list)
        for entry in candidates:
            self.assertEqual(entry["grounded_citations"], 1)
            self.assertTrue(entry["unscored_content"])

    def test_extra_candidate_is_unscored_without_changing_approval(self) -> None:
        doc = make_document()
        refs = (make_item("ref-1", "eligibility", (cite(doc, LINE1, "line 1"),)),)
        cands = (
            make_item("cand-match", "eligibility", (cite(doc, LINE1, "line 1"),)),
            make_item("cand-extra", "eligibility", (cite(doc, LINE4, "line 4"),)),
        )
        result = evaluate_cases((make_case("case-extra", doc, doc, refs, cands),))
        case = first_case(result)
        items = case["items"]
        assert isinstance(items, list)
        self.assertEqual(items[0]["status"], "covered")
        self.assertEqual(items[0]["candidate_ids"], ["cand-match"])
        candidates = case["candidates"]
        assert isinstance(candidates, list)
        scored = {entry["id"]: entry["unscored_content"] for entry in candidates}
        self.assertFalse(scored["cand-match"])
        self.assertTrue(scored["cand-extra"])
        summary = result["summary"]
        assert isinstance(summary, dict)
        self.assertEqual(summary["approved_evidence_recall"], 1.0)
        self.assertEqual(summary["unscored_candidates"], 1)
        self.assertEqual(result["assessment"], "no_findings")
        self.assertEqual(result["scope"], "approved_reference_only")
        self.assertTrue(result["requires_human_review"])
        self.assertEqual(result["approval_effect"], "none")

    def test_amount_value_change_is_flagged(self) -> None:
        doc = make_document()
        refs = (
            make_item(
                "ref-amt1",
                "amount",
                (cite(doc, LINE1, "line 1"),),
                amounts_won=(500000,),
            ),
            make_item(
                "ref-amt2",
                "amount",
                (cite(doc, LINE3, "line 3"),),
                amounts_won=(100000000,),
            ),
        )
        cands = (
            make_item(
                "cand-amt1",
                "amount",
                (cite(doc, LINE1, "line 1"),),
                amounts_won=(600000,),
            ),
            make_item(
                "cand-amt2",
                "amount",
                (cite(doc, LINE3, "line 3"),),
                amounts_won=(100000000,),
            ),
        )
        result = evaluate_cases((make_case("case-amount", doc, doc, refs, cands),))
        items = first_case(result)["items"]
        assert isinstance(items, list)
        self.assertEqual(items[0]["status"], "covered")
        self.assertFalse(items[0]["amount_values_match"])
        self.assertEqual(items[1]["status"], "covered")
        self.assertTrue(items[1]["amount_values_match"])
        summary = result["summary"]
        assert isinstance(summary, dict)
        self.assertEqual(summary["amount_value_mismatches"], 1)

    def test_invalid_reference_exception_is_rejected(self) -> None:
        doc = make_document()
        bad_ref = make_item(
            "ref-bad-exc",
            "rule",
            (cite(doc, LINE1, "line 1"),),
            target_policy_ids=("p-a",),
            rule_kind=RULE_KIND,
            direction=BOTH,
            exceptions=("어디에도 없는 예외 문구",),
        )
        cand = make_item(
            "cand-1",
            "rule",
            (cite(doc, LINE1, "line 1"),),
            target_policy_ids=("p-a",),
            rule_kind=RULE_KIND,
            direction=BOTH,
        )
        with self.assertRaisesRegex(ValueError, "unsupported_f12_reference_exception"):
            evaluate_cases((make_case("case-bad-exc", doc, doc, (bad_ref,), (cand,)),))

    def test_split_citations_and_amounts_together_match(self) -> None:
        doc = make_document()
        refs = (
            make_item(
                "ref-split",
                "amount",
                (cite(doc, LINE1, "line 1"), cite(doc, LINE3, "line 3")),
                amounts_won=(500000, 100000000),
            ),
        )
        cands = (
            make_item(
                "cand-s1",
                "amount",
                (cite(doc, LINE1, "line 1"),),
                amounts_won=(500000,),
            ),
            make_item(
                "cand-s2",
                "amount",
                (cite(doc, LINE3, "line 3"),),
                amounts_won=(100000000,),
            ),
        )
        result = evaluate_cases((make_case("case-split", doc, doc, refs, cands),))
        items = first_case(result)["items"]
        assert isinstance(items, list)
        self.assertEqual(items[0]["status"], "covered")
        self.assertTrue(items[0]["amount_values_match"])
        summary = result["summary"]
        assert isinstance(summary, dict)
        self.assertEqual(summary["amount_value_mismatches"], 0)

    def test_extraction_failure_is_not_comparable_without_approval_recall(self) -> None:
        doc = make_document()
        passages = (
            (LINE1, "line 1"),
            (LINE2, "line 2"),
            (LINE3, "line 3"),
            (LINE4, "line 4"),
        )
        normal_cases = []
        for index in range(1, 5):
            case_id = f"case-n{index}"
            refs = tuple(
                make_item(f"{case_id}-ref-{pos + 1}", "eligibility", (cite(doc, quote, loc),))
                for pos, (quote, loc) in enumerate(passages)
            )
            cands = tuple(
                make_item(f"{case_id}-cand-{pos + 1}", "eligibility", (cite(doc, quote, loc),))
                for pos, (quote, loc) in enumerate(passages)
            )
            normal_cases.append(make_case(case_id, doc, doc, refs, cands))
        failed_refs = tuple(
            make_item(f"failed-ref-{pos + 1}", "eligibility", (cite(doc, quote, loc),))
            for pos, (quote, loc) in enumerate(passages)
        )
        failed = EvaluationCase(
            "case-failed", "employment", doc, None, failed_refs, (), "context_limit"
        )
        result = evaluate_cases((*normal_cases, failed))
        summary = result["summary"]
        assert isinstance(summary, dict)
        self.assertEqual(summary["failed_extractions"], 1)
        self.assertEqual(summary["not_comparable_items"], 4)
        self.assertEqual(summary["evaluation_coverage"], 0.8)
        self.assertEqual(summary["comparable_evidence_recall"], 1.0)
        self.assertIsNone(summary["approved_evidence_recall"])
        cases = result["cases"]
        assert isinstance(cases, list)
        failed_result = [c for c in cases if c["id"] == "case-failed"][0]
        assert isinstance(failed_result, dict)
        self.assertFalse(failed_result["comparable"])
        self.assertIsNone(failed_result["candidate_source"])
        self.assertEqual(failed_result["extraction_error"], "context_limit")
        self.assertEqual(failed_result["candidates"], [])
        statuses = [item["status"] for item in failed_result["items"]]
        self.assertEqual(statuses, ["not_comparable"] * 4)
