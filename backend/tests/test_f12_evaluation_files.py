"""F12 evaluation file adapter regressions with virtual fixtures only.

Reference and candidate pending snapshots use public write_rule_snapshot and
prepare_snapshot with zero rules. Candidate facts, approved exports, and case
manifests are virtual JSON written directly. No real source text, network, DB,
.env, or data/local access.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from benefitfinder.application.policy_rules import CandidateReview
from benefitfinder.application.policy_rules import PolicyReference
from benefitfinder.application.policy_rules import ReviewOperation
from benefitfinder.application.policy_rules import RuleApproval
from benefitfinder.application.policy_rules import RuleCandidate
from benefitfinder.application.policy_rules import RuleCitation
from benefitfinder.application.policy_rules import RuleDirection
from benefitfinder.application.policy_rules import RuleKind
from benefitfinder.application.policy_rules import RuleReviewBatch
from benefitfinder.application.policy_rules import RuleReviewSnapshot
from benefitfinder.application.policy_rules import SourceDocument
from benefitfinder.infrastructure.f12_evaluation_files import EvaluationFileError
from benefitfinder.infrastructure.f12_evaluation_files import evaluate_manifest
from benefitfinder.infrastructure.f12_evaluation_files import load_evaluation_cases
from benefitfinder.infrastructure.f12_evaluation_files import write_evaluation_report
from benefitfinder.infrastructure.policy_rule_files import write_rule_snapshot
from benefitfinder.ingestion.rule_review_workflow import prepare_snapshot

UTC = timezone.utc
BASE_COLLECTED = datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
REVIEWED_AT = datetime(2026, 10, 27, 0, 0, tzinfo=UTC)
CREATED_AT = datetime(2026, 10, 28, 0, 0, tzinfo=UTC)

LINE1 = "청년 취업 지원금은 월 50만원이다."
LINE2 = "중복 수혜는 불가하며 예외는 별도 표기한다."
LINE3 = "주거 지원은 보증금 대출 1억원을 제공한다."
LINE4 = "신청은 온라인으로 하며 마감은 12월이다."
DOC_TEXT = LINE1 + "\n" + LINE2 + "\n" + LINE3 + "\n" + LINE4 + "\n"

POLICY_ID = "youth-employment-virtual-01"
DOC_ID = "doc-virtual-01"
VERSION = "v2026-01"


def make_policy(policy_id: str = POLICY_ID) -> PolicyReference:
    return PolicyReference(id=policy_id, domain="youth", categories=("employment",))


def make_document(
    doc_id: str = DOC_ID,
    version: str = VERSION,
    text: str = DOC_TEXT,
    official: bool = True,
) -> SourceDocument:
    return SourceDocument(
        id=doc_id,
        policy_id=POLICY_ID,
        source_url="https://example.invalid/docs/" + doc_id,
        document_version=version,
        collected_at=BASE_COLLECTED,
        text=text,
        official=official,
    )


def write_pending(directory: Path, document: SourceDocument, policy: PolicyReference) -> None:
    batch = RuleReviewBatch(documents=(document,), policies=(policy,), candidates=())
    snapshot = prepare_snapshot(batch, at=CREATED_AT)
    write_rule_snapshot(directory / "rules-pending.json", snapshot)


def citation_dict(document: SourceDocument, quote: str, location: str) -> dict[str, object]:
    return {
        "document_id": document.id,
        "document_version": document.document_version,
        "content_sha256": document.content_sha256,
        "quote": quote,
        "location": location,
    }


def source_dict(document: SourceDocument) -> dict[str, object]:
    return {
        "id": document.id,
        "url": document.source_url,
        "document_version": document.document_version,
        "content_sha256": document.content_sha256,
        "collected_at": document.collected_at.isoformat(),
    }


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def valid_facts(document: SourceDocument, quote: str = LINE1) -> dict[str, object]:
    return {
        "format_version": 1,
        "policy_id": POLICY_ID,
        "source": source_dict(document),
        "status": "needs_review",
        "eligibility": {"status": "not_found", "items": []},
        "benefit_amount": {
            "status": "extracted",
            "items": [
                {
                    "evidence": citation_dict(document, quote, "line 1"),
                    "amounts_won": [500000],
                }
            ],
        },
        "payment_timing": {"status": "not_found", "items": [], "scheduled_monthly_day": None},
    }


def gold_amount_item(
    document: SourceDocument,
    item_id: str = "amount:1",
    quote: str = LINE1,
    amounts: list[int] | None = None,
) -> dict[str, object]:
    return {
        "item_id": item_id,
        "kind": "amount",
        "approval": {
            "reviewer": "virtual-reviewer",
            "reason": "virtual review",
            "reviewed_at": REVIEWED_AT.isoformat(),
        },
        "candidate": {
            "evidence": citation_dict(document, quote, "line 1"),
            "amounts_won": amounts if amounts is not None else [500000],
        },
    }


def valid_gold(document: SourceDocument) -> dict[str, object]:
    return {
        "format_version": 1,
        "kind": "f12_approved_review_export",
        "pair_sha256": "a" * 64,
        "policy_id": POLICY_ID,
        "source": source_dict(document),
        "items": [gold_amount_item(document)],
    }


def setup_valid_tree(base: Path) -> tuple[Path, SourceDocument, SourceDocument]:
    doc_ref = make_document()
    doc_cand = make_document()
    policy = make_policy()
    write_pending(base / "ref", doc_ref, policy)
    write_pending(base / "cand", doc_cand, policy)
    write_json(base / "cand" / "facts-pending.json", valid_facts(doc_cand))
    write_json(base / "gold.json", valid_gold(doc_ref))
    manifest = {
        "format_version": 1,
        "cases": [
            {
                "id": "case-01",
                "category": "employment",
                "reference_dir": "ref",
                "approved_exports": ["gold.json"],
                "candidate_dir": "cand",
            }
        ],
    }
    manifest_path = base / "manifest.json"
    write_json(manifest_path, manifest)
    return manifest_path, doc_ref, doc_cand


class TestF12EvaluationFiles(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)

    def test_valid_tree_uses_rules_pending_only_for_reference(self) -> None:
        base = self.root / "valid"
        manifest, _, _ = setup_valid_tree(base)
        self.assertTrue((base / "ref" / "rules-pending.json").is_file())
        self.assertFalse((base / "ref" / "facts-pending.json").exists())
        cases = load_evaluation_cases(manifest)
        self.assertEqual(len(cases), 1)
        self.assertEqual(len(cases[0].references), 1)
        self.assertEqual(len(cases[0].candidates), 1)
        report = evaluate_manifest(manifest)
        self.assertEqual(report["assessment"], "no_findings")
        summary = report["summary"]
        assert isinstance(summary, dict)
        self.assertEqual(summary["covered_items"], 1)
        self.assertEqual(summary["approved_evidence_recall"], 1.0)

    def test_rejects_unprepared_or_approved_pending(self) -> None:
        base_a = self.root / "badop"
        manifest_a, _, doc_cand_a = setup_valid_tree(base_a)
        batch = RuleReviewBatch(
            documents=(doc_cand_a,), policies=(make_policy(),), candidates=()
        )
        bad_operation = RuleReviewSnapshot(
            batch=batch, reviews=(), created_at=CREATED_AT, operation=ReviewOperation.APPROVED
        )
        (base_a / "cand" / "rules-pending.json").unlink()
        write_rule_snapshot(base_a / "cand" / "rules-pending.json", bad_operation)
        with self.assertRaises(EvaluationFileError):
            load_evaluation_cases(manifest_a)
        base_b = self.root / "badapproval"
        manifest_b, _, doc_cand_b = setup_valid_tree(base_b)
        citation = RuleCitation(
            doc_cand_b.id,
            doc_cand_b.document_version,
            doc_cand_b.content_sha256,
            LINE1,
            "line 1",
        )
        candidate = RuleCandidate(
            id="cand-01",
            source_policy_id=POLICY_ID,
            target_policy_ids=("p-target",),
            kind=RuleKind.SIMULTANEOUS_EXCLUSION,
            description="virtual rule",
            citations=(citation,),
            direction=RuleDirection.BOTH,
        )
        approval = RuleApproval(
            candidate_sha256="a" * 64,
            documents_sha256="b" * 64,
            reviewer="reviewer",
            reviewed_at=REVIEWED_AT,
        )
        review = CandidateReview(candidate=candidate, issues=(), approval=approval)
        batch_b = RuleReviewBatch(
            documents=(doc_cand_b,), policies=(make_policy(),), candidates=(candidate,)
        )
        snapshot_b = RuleReviewSnapshot(
            batch=batch_b, reviews=(review,), created_at=CREATED_AT, operation=ReviewOperation.PREPARED
        )
        (base_b / "cand" / "rules-pending.json").unlink()
        write_rule_snapshot(base_b / "cand" / "rules-pending.json", snapshot_b)
        with self.assertRaises(EvaluationFileError):
            load_evaluation_cases(manifest_b)

    def test_rejects_source_mismatch_and_bad_fact_status(self) -> None:
        base_gold = self.root / "mismatch-gold"
        manifest_gold, doc_ref_gold, _ = setup_valid_tree(base_gold)
        bad_gold = valid_gold(doc_ref_gold)
        assert isinstance(bad_gold["source"], dict)
        bad_gold["source"] = {**bad_gold["source"], "document_version": "v999"}
        write_json(base_gold / "gold.json", bad_gold)
        with self.assertRaises(EvaluationFileError):
            load_evaluation_cases(manifest_gold)
        base_facts = self.root / "mismatch-facts"
        manifest_facts, _, doc_cand_facts = setup_valid_tree(base_facts)
        bad_facts = valid_facts(doc_cand_facts)
        assert isinstance(bad_facts["source"], dict)
        bad_facts["source"] = {**bad_facts["source"], "content_sha256": "b" * 64}
        write_json(base_facts / "cand" / "facts-pending.json", bad_facts)
        with self.assertRaises(EvaluationFileError):
            load_evaluation_cases(manifest_facts)
        base_status = self.root / "bad-status"
        manifest_status, _, doc_cand_status = setup_valid_tree(base_status)
        rejected_facts = valid_facts(doc_cand_status)
        assert isinstance(rejected_facts["benefit_amount"], dict)
        rejected_facts["benefit_amount"] = {
            **rejected_facts["benefit_amount"],
            "status": "rejected",
        }
        write_json(base_status / "cand" / "facts-pending.json", rejected_facts)
        with self.assertRaises(EvaluationFileError):
            load_evaluation_cases(manifest_status)
        base_kind = self.root / "bad-kind"
        manifest_kind, doc_ref_kind, _ = setup_valid_tree(base_kind)
        bad_kind = valid_gold(doc_ref_kind)
        bad_kind["kind"] = "other_kind"
        write_json(base_kind / "gold.json", bad_kind)
        with self.assertRaises(EvaluationFileError):
            load_evaluation_cases(manifest_kind)
        base_noapproval = self.root / "no-approval"
        manifest_noapproval, doc_ref_noapproval, _ = setup_valid_tree(base_noapproval)
        no_approval = valid_gold(doc_ref_noapproval)
        assert isinstance(no_approval["items"], list)
        first_item = dict(no_approval["items"][0])
        assert isinstance(first_item, dict)
        del first_item["approval"]
        no_approval["items"] = [first_item]
        write_json(base_noapproval / "gold.json", no_approval)
        with self.assertRaises(EvaluationFileError):
            load_evaluation_cases(manifest_noapproval)

    def test_rejects_manifest_shape_problems(self) -> None:
        dup_path = self.root / "dup" / "manifest.json"
        dup_path.parent.mkdir(parents=True, exist_ok=True)
        dup_path.write_text(
            '{"format_version": 1, "format_version": 1, "cases": []}', encoding="utf-8"
        )
        with self.assertRaises(EvaluationFileError):
            load_evaluation_cases(dup_path)
        bool_path = self.root / "boolver" / "manifest.json"
        bool_path.parent.mkdir(parents=True, exist_ok=True)
        bool_path.write_text(
            json.dumps({"format_version": True, "cases": []}), encoding="utf-8"
        )
        with self.assertRaises(EvaluationFileError):
            load_evaluation_cases(bool_path)
        base_same = self.root / "samedir"
        manifest_same, _, _ = setup_valid_tree(base_same)
        same_manifest = {
            "format_version": 1,
            "cases": [
                {
                    "id": "case-01",
                    "category": "employment",
                    "reference_dir": "ref",
                    "approved_exports": ["gold.json"],
                    "candidate_dir": "ref",
                }
            ],
        }
        write_json(manifest_same, same_manifest)
        with self.assertRaises(EvaluationFileError):
            load_evaluation_cases(manifest_same)

    def test_rejects_duplicate_gold_ids(self) -> None:
        base = self.root / "dupgold"
        _, doc_ref, _ = setup_valid_tree(base)
        write_json(base / "gold1.json", valid_gold(doc_ref))
        write_json(base / "gold2.json", valid_gold(doc_ref))
        manifest = {
            "format_version": 1,
            "cases": [
                {
                    "id": "case-01",
                    "category": "employment",
                    "reference_dir": "ref",
                    "approved_exports": ["gold1.json", "gold2.json"],
                    "candidate_dir": "cand",
                }
            ],
        }
        manifest_path = base / "manifest.json"
        write_json(manifest_path, manifest)
        cases = load_evaluation_cases(manifest_path)
        self.assertEqual(len(cases[0].references), 2)
        with self.assertRaises(EvaluationFileError):
            evaluate_manifest(manifest_path)

    def test_bad_candidate_quote_is_finding_not_error(self) -> None:
        base = self.root / "badquote"
        manifest, _, doc_cand = setup_valid_tree(base)
        bad_facts = valid_facts(doc_cand, quote="가상 존재하지 않는 문구 999")
        write_json(base / "cand" / "facts-pending.json", bad_facts)
        cases = load_evaluation_cases(manifest)
        self.assertEqual(len(cases), 1)
        report = evaluate_manifest(manifest)
        self.assertEqual(report["assessment"], "findings")
        summary = report["summary"]
        assert isinstance(summary, dict)
        self.assertEqual(summary["citation_errors"], 1)
        first = report["cases"]
        assert isinstance(first, list)
        case = first[0]
        assert isinstance(case, dict)
        items = case["items"]
        assert isinstance(items, list)
        self.assertEqual(items[0]["status"], "missing")
        candidates = case["candidates"]
        assert isinstance(candidates, list)
        self.assertEqual(candidates[0]["errors"][0]["code"], "quote_not_in_source")

    def test_write_report_is_create_only_and_cleans_temp(self) -> None:
        base = self.root / "report"
        manifest, _, _ = setup_valid_tree(base)
        report = evaluate_manifest(manifest)
        target = base / "nested" / "report.json"
        write_evaluation_report(target, report)
        self.assertTrue(target.is_file())
        first_text = target.read_text(encoding="utf-8")
        with self.assertRaises(OSError):
            write_evaluation_report(target, {"format_version": 1})
        self.assertEqual(target.read_text(encoding="utf-8"), first_text)
        leftovers = list(target.parent.glob(".f12-evaluation-*"))
        self.assertEqual(leftovers, [])

    def test_context_limit_failure_reports_coverage_without_approval_recall(self) -> None:
        base = self.root / "failed"
        doc = make_document()
        policy = make_policy()
        write_pending(base / "ref", doc, policy)
        write_pending(base / "cand", doc, policy)
        amounts = [500000, 600000, 700000, 800000]
        passages = [(LINE1, "line 1"), (LINE2, "line 2"), (LINE3, "line 3"), (LINE4, "line 4")]
        facts_items = [
            {"evidence": citation_dict(doc, quote, loc), "amounts_won": [amount]}
            for (quote, loc), amount in zip(passages, amounts)
        ]
        write_json(
            base / "cand" / "facts-pending.json",
            {
                "format_version": 1,
                "policy_id": POLICY_ID,
                "source": source_dict(doc),
                "status": "needs_review",
                "eligibility": {"status": "not_found", "items": []},
                "benefit_amount": {"status": "extracted", "items": facts_items},
                "payment_timing": {
                    "status": "not_found",
                    "items": [],
                    "scheduled_monthly_day": None,
                },
            },
        )
        gold_items = [
            {
                "item_id": f"amount:{pos + 1}",
                "kind": "amount",
                "approval": {
                    "reviewer": "virtual-reviewer",
                    "reason": "virtual review",
                    "reviewed_at": REVIEWED_AT.isoformat(),
                },
                "candidate": {
                    "evidence": citation_dict(doc, quote, loc),
                    "amounts_won": [amount],
                },
            }
            for pos, ((quote, loc), amount) in enumerate(zip(passages, amounts))
        ]
        write_json(
            base / "gold.json",
            {
                "format_version": 1,
                "kind": "f12_approved_review_export",
                "pair_sha256": "a" * 64,
                "policy_id": POLICY_ID,
                "source": source_dict(doc),
                "items": gold_items,
            },
        )
        failed_doc = make_document()
        write_pending(base / "ref-failed", failed_doc, policy)
        failed_gold_items = [
            {
                "item_id": f"amount:{pos + 1}",
                "kind": "amount",
                "approval": {
                    "reviewer": "virtual-reviewer",
                    "reason": "virtual review",
                    "reviewed_at": REVIEWED_AT.isoformat(),
                },
                "candidate": {
                    "evidence": citation_dict(failed_doc, quote, loc),
                    "amounts_won": [amount],
                },
            }
            for pos, ((quote, loc), amount) in enumerate(zip(passages, amounts))
        ]
        write_json(
            base / "gold-failed.json",
            {
                "format_version": 1,
                "kind": "f12_approved_review_export",
                "pair_sha256": "b" * 64,
                "policy_id": POLICY_ID,
                "source": source_dict(failed_doc),
                "items": failed_gold_items,
            },
        )
        manifest_cases = [
            {
                "id": f"case-n{index}",
                "category": "employment",
                "reference_dir": "ref",
                "approved_exports": ["gold.json"],
                "candidate_dir": "cand",
            }
            for index in range(1, 5)
        ]
        manifest_cases.append(
            {
                "id": "case-failed",
                "category": "employment",
                "reference_dir": "ref-failed",
                "approved_exports": ["gold-failed.json"],
                "candidate_dir": None,
                "candidate_error": "context_limit",
            }
        )
        manifest_path = base / "manifest.json"
        write_json(manifest_path, {"format_version": 1, "cases": manifest_cases})
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert isinstance(raw, dict)
        raw_cases = raw["cases"]
        assert isinstance(raw_cases, list)
        self.assertEqual(
            set(raw_cases[0].keys()),
            {"id", "category", "reference_dir", "approved_exports", "candidate_dir"},
        )
        loaded = load_evaluation_cases(manifest_path)
        self.assertEqual(len(loaded), 5)
        failed_case = [c for c in loaded if c.id == "case-failed"][0]
        self.assertIsNone(failed_case.candidate_document)
        self.assertEqual(failed_case.extraction_error, "context_limit")
        self.assertEqual(failed_case.candidates, ())
        report = evaluate_manifest(manifest_path)
        summary = report["summary"]
        assert isinstance(summary, dict)
        self.assertEqual(summary["failed_extractions"], 1)
        self.assertEqual(summary["not_comparable_items"], 4)
        self.assertEqual(summary["evaluation_coverage"], 0.8)
        self.assertEqual(summary["comparable_evidence_recall"], 1.0)
        self.assertIsNone(summary["approved_evidence_recall"])
        first = report["cases"]
        assert isinstance(first, list)
        failed_result = [c for c in first if c["id"] == "case-failed"][0]
        assert isinstance(failed_result, dict)
        self.assertFalse(failed_result["comparable"])
        self.assertIsNone(failed_result["candidate_source"])
        self.assertEqual(failed_result["extraction_error"], "context_limit")
