"""Local F12 fact file tests with virtual fixtures only."""
from __future__ import annotations
import json
import tempfile
import unittest
from datetime import datetime
from datetime import timezone
from pathlib import Path
from benefitfinder.application.policy_facts import PolicyAmountCandidate
from benefitfinder.application.policy_facts import PolicyFactsDraft
from benefitfinder.application.policy_rules import RuleCitation
from benefitfinder.application.policy_rules import SourceDocument
from benefitfinder.infrastructure.policy_rule_files import RuleReviewFileError
from benefitfinder.infrastructure.policy_rule_files import write_policy_facts
UTC = timezone.utc
COLLECTED = datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
DOC_ID = "doc-virtual-facts-01"
POLICY_ID = "youth-employment-virtual-01"
QUOTE_ELIGIBILITY = "VIRTUAL_QUOTE_FACTS_001"
QUOTE_AMOUNT = "VIRTUAL_QUOTE_AMOUNT_001"
QUOTE_TIMING = "VIRTUAL_QUOTE_TIMING_001"
LOCATION = "section 3 virtual"
def make_document() -> SourceDocument:
    return SourceDocument(
        id=DOC_ID,
        policy_id=POLICY_ID,
        source_url="https://example.invalid/docs/doc-virtual-facts-01",
        document_version="v2026-01",
        collected_at=COLLECTED,
        text="virtual notice VIRTUAL_QUOTE_FACTS_001 VIRTUAL_QUOTE_AMOUNT_001 VIRTUAL_QUOTE_TIMING_001 unicode",
        official=True,
    )
def make_citation(document: SourceDocument, quote: str = QUOTE_ELIGIBILITY) -> RuleCitation:
    return RuleCitation(
        document_id=document.id,
        document_version=document.document_version,
        content_sha256=document.content_sha256,
        quote=quote,
        location=LOCATION,
    )
def make_full_draft(document: SourceDocument) -> PolicyFactsDraft:
    return PolicyFactsDraft(
        document=document,
        eligibility=(make_citation(document, QUOTE_ELIGIBILITY),),
        amounts=(
            PolicyAmountCandidate(
                evidence=make_citation(document, QUOTE_AMOUNT),
                amounts_won=(200000, 300000),
            ),
        ),
        payment_timing=(make_citation(document, QUOTE_TIMING),),
        scheduled_monthly_day=20,
    )
class PolicyFactsFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
    def test_provenance_and_fixed_keys(self) -> None:
        document = make_document()
        draft = make_full_draft(document)
        path = self.root / "facts.json"
        write_policy_facts(path, draft)
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(
            set(raw.keys()),
            {"format_version", "policy_id", "status", "source", "eligibility", "benefit_amount", "payment_timing"},
        )
        self.assertEqual(raw["format_version"], 1)
        self.assertEqual(raw["status"], "needs_review")
        self.assertEqual(raw["policy_id"], POLICY_ID)
        source = raw["source"]
        self.assertEqual(
            set(source.keys()),
            {"id", "url", "document_version", "content_sha256", "collected_at"},
        )
        self.assertEqual(source["id"], DOC_ID)
        self.assertEqual(source["url"], "https://example.invalid/docs/doc-virtual-facts-01")
        self.assertEqual(source["document_version"], "v2026-01")
        self.assertEqual(source["content_sha256"], document.content_sha256)
        self.assertEqual(source["collected_at"], COLLECTED.isoformat())
        self.assertNotIn("text", raw)
        self.assertNotIn("text", source)
        self.assertNotIn("approval", raw)
        self.assertNotIn("official", source)
        eligibility = raw["eligibility"]
        self.assertEqual(eligibility["status"], "extracted")
        self.assertEqual(len(eligibility["items"]), 1)
        self.assertEqual(eligibility["items"][0]["evidence"]["quote"], QUOTE_ELIGIBILITY)
        self.assertEqual(eligibility["items"][0]["evidence"]["document_id"], DOC_ID)
        benefit = raw["benefit_amount"]
        self.assertEqual(benefit["status"], "extracted")
        self.assertEqual(len(benefit["items"]), 1)
        self.assertEqual(benefit["items"][0]["amounts_won"], [200000, 300000])
        self.assertEqual(benefit["items"][0]["evidence"]["quote"], QUOTE_AMOUNT)
        timing = raw["payment_timing"]
        self.assertEqual(timing["status"], "extracted")
        self.assertEqual(len(timing["items"]), 1)
        self.assertEqual(timing["items"][0]["evidence"]["quote"], QUOTE_TIMING)
        self.assertEqual(timing["scheduled_monthly_day"], 20)
    def test_missing_values_marked_not_found(self) -> None:
        document = make_document()
        draft = PolicyFactsDraft(
            document=document,
            eligibility=(),
            amounts=(),
            payment_timing=(),
            scheduled_monthly_day=None,
        )
        path = self.root / "facts-empty.json"
        write_policy_facts(path, draft)
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(raw["eligibility"]["status"], "not_found")
        self.assertEqual(raw["eligibility"]["items"], [])
        self.assertEqual(raw["benefit_amount"]["status"], "not_found")
        self.assertEqual(raw["benefit_amount"]["items"], [])
        self.assertEqual(raw["payment_timing"]["status"], "not_found")
        self.assertEqual(raw["payment_timing"]["items"], [])
        self.assertIsNone(raw["payment_timing"]["scheduled_monthly_day"])
        self.assertEqual(raw["status"], "needs_review")
    def test_orphan_day_rejected_without_output(self) -> None:
        document = make_document()
        draft = PolicyFactsDraft(
            document=document,
            eligibility=(),
            amounts=(),
            payment_timing=(),
            scheduled_monthly_day=15,
        )
        path = self.root / "facts-day.json"
        with self.assertRaises(RuleReviewFileError) as ctx:
            write_policy_facts(path, draft)
        self.assertEqual(str(ctx.exception), "invalid_rule_review_file")
        self.assertFalse(path.exists())
    def test_create_only_preserves_first(self) -> None:
        document = make_document()
        first = make_full_draft(document)
        path = self.root / "facts-once.json"
        write_policy_facts(path, first)
        before = path.read_bytes()
        second = PolicyFactsDraft(
            document=document,
            eligibility=(),
            amounts=(),
            payment_timing=(),
            scheduled_monthly_day=None,
        )
        with self.assertRaises(RuleReviewFileError) as ctx:
            write_policy_facts(path, second)
        self.assertEqual(str(ctx.exception), "rule_review_output_exists")
        self.assertEqual(path.read_bytes(), before)
if __name__ == "__main__":
    unittest.main()
