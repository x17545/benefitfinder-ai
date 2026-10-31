"""Local rule-review file adapter tests with virtual fixtures only."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
import unittest
from datetime import datetime
from datetime import timezone
from pathlib import Path

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
from benefitfinder.infrastructure.policy_rule_files import RuleReviewFileError
from benefitfinder.infrastructure.policy_rule_files import load_rule_batch
from benefitfinder.infrastructure.policy_rule_files import load_rule_snapshot
from benefitfinder.infrastructure.policy_rule_files import write_rule_batch
from benefitfinder.infrastructure.policy_rule_files import write_rule_snapshot

UTC = timezone.utc
BASE_COLLECTED = datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
REVIEWED_AT = datetime(2026, 10, 27, 0, 0, tzinfo=UTC)
CREATED_AT = datetime(2026, 10, 28, 0, 0, tzinfo=UTC)
QUOTE = "VIRTUAL_QUOTE_001"

EMPLOYMENT_ID = "youth-employment-virtual-01"
HOUSING_ID = "youth-housing-virtual-02"


def make_policy(policy_id: str = EMPLOYMENT_ID) -> PolicyReference:
    if policy_id == HOUSING_ID:
        categories: tuple[str, ...] = ("housing",)
    else:
        categories = ("employment",)
    return PolicyReference(id=policy_id, domain="youth", categories=categories)


def make_document(doc_id: str = "doc-virtual-01") -> SourceDocument:
    if doc_id == "doc-virtual-02":
        policy_id = HOUSING_ID
    else:
        policy_id = EMPLOYMENT_ID
    return SourceDocument(
        id=doc_id,
        policy_id=policy_id,
        source_url="https://example.invalid/docs/" + doc_id,
        document_version="v2026-01",
        collected_at=BASE_COLLECTED,
        text="virtual notice " + QUOTE + " unicode 한글 leaf",
        official=True,
    )


def citation_for(document: SourceDocument) -> RuleCitation:
    return RuleCitation(
        document_id=document.id,
        document_version=document.document_version,
        content_sha256=document.content_sha256,
        quote=QUOTE,
        location="section 3",
    )


def make_candidate(doc_a: SourceDocument, doc_b: SourceDocument) -> RuleCandidate:
    return RuleCandidate(
        id="candidate-virtual-01",
        source_policy_id=EMPLOYMENT_ID,
        target_policy_ids=(HOUSING_ID,),
        kind=RuleKind.SIMULTANEOUS_EXCLUSION,
        description="virtual rule unicode 한글",
        citations=(citation_for(doc_a), citation_for(doc_b)),
        direction=RuleDirection.BOTH,
    )


def make_batch() -> RuleReviewBatch:
    doc_a = make_document("doc-virtual-01")
    doc_b = make_document("doc-virtual-02")
    policies = (make_policy(EMPLOYMENT_ID), make_policy(HOUSING_ID))
    candidate = make_candidate(doc_a, doc_b)
    return RuleReviewBatch(documents=(doc_a, doc_b), policies=policies, candidates=(candidate,))


def make_snapshot(approved: bool = False) -> RuleReviewSnapshot:
    batch = make_batch()
    candidate = batch.candidates[0]
    if approved:
        approval = RuleApproval(
            candidate_sha256="a" * 64,
            documents_sha256="b" * 64,
            reviewer="seo-yun",
            reviewed_at=REVIEWED_AT,
        )
        issues: tuple[ReviewIssue, ...] = ()
    else:
        approval = None
        issues = (ReviewIssue(code="virtual-check", document_id=None),)
    review = CandidateReview(candidate=candidate, issues=issues, approval=approval)
    return RuleReviewSnapshot(
        batch=batch, reviews=(review,), created_at=CREATED_AT, operation=ReviewOperation.PREPARED
    )


class BatchRoundtripTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)

    def test_batch_roundtrip_minimal_defaults(self) -> None:
        doc = make_document("doc-virtual-01")
        batch = RuleReviewBatch(
            documents=(doc,),
            policies=(make_policy(EMPLOYMENT_ID),),
            candidates=(),
        )
        path = self.root / "batch.json"
        write_rule_batch(path, batch)
        loaded = load_rule_batch(path)
        self.assertEqual(loaded, batch)

    def test_batch_roundtrip_full_optional_unicode(self) -> None:
        doc_a = make_document("doc-virtual-01")
        doc_b = make_document("doc-virtual-02")
        candidate = RuleCandidate(
            id="candidate-full-01",
            source_policy_id=EMPLOYMENT_ID,
            target_policy_ids=(HOUSING_ID,),
            kind=RuleKind.CONDITIONAL,
            description="conditional unicode 한글 description",
            citations=(citation_for(doc_a),),
            direction=RuleDirection.SOURCE_TO_TARGET,
            conditions=("virtual condition 1",),
            exceptions=("virtual exception",),
            period="2026",
            amount_effect="virtual effect",
            unresolved=("virtual unresolved",),
        )
        batch = RuleReviewBatch(
            documents=(doc_a, doc_b),
            policies=(make_policy(EMPLOYMENT_ID), make_policy(HOUSING_ID)),
            candidates=(candidate,),
        )
        path = self.root / "nested" / "batch-full.json"
        write_rule_batch(path, batch)
        loaded = load_rule_batch(path)
        self.assertEqual(loaded, batch)
        raw = path.read_text(encoding="utf-8")
        self.assertIn("unicode", raw)

    def test_snapshot_roundtrip_needs_review(self) -> None:
        snapshot = make_snapshot(approved=False)
        path = self.root / "snapshot-needs.json"
        write_rule_snapshot(path, snapshot)
        loaded = load_rule_snapshot(path)
        self.assertEqual(loaded, snapshot)
        self.assertEqual(loaded.reviews[0].status, "needs_review")

    def test_snapshot_roundtrip_approved_meta(self) -> None:
        snapshot = make_snapshot(approved=True)
        path = self.root / "snapshot-approved.json"
        write_rule_snapshot(path, snapshot)
        loaded = load_rule_snapshot(path)
        self.assertEqual(loaded, snapshot)
        approval = loaded.reviews[0].approval
        self.assertIsNotNone(approval)
        assert approval is not None
        self.assertEqual(approval.reviewer, "seo-yun")
        self.assertEqual(approval.reviewed_at, REVIEWED_AT)
        self.assertEqual(loaded.reviews[0].status, "approved")

    def test_snapshot_preserves_timezone_offset(self) -> None:
        batch = make_batch()
        created = datetime.fromisoformat("2026-10-28T09:00:00+09:00")
        review = CandidateReview(candidate=batch.candidates[0], issues=())
        snapshot = RuleReviewSnapshot(
            batch=batch,
            reviews=(review,),
            created_at=created,
            operation=ReviewOperation.APPROVED,
        )
        path = self.root / "snapshot-tz.json"
        write_rule_snapshot(path, snapshot)
        loaded = load_rule_snapshot(path)
        self.assertEqual(loaded.created_at, created)
        self.assertIsNotNone(loaded.created_at.utcoffset())


class StrictValidationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)

    def write_raw(self, name: str, content: str) -> Path:
        path = self.root / name
        path.write_text(content, encoding="utf-8")
        return path

    def valid_batch_dict(self) -> dict[str, object]:
        batch = make_batch()
        path = self.root / "valid.json"
        write_rule_batch(path, batch)
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        assert isinstance(data, dict)
        return data

    def test_rejects_unknown_top_field_profile(self) -> None:
        data = self.valid_batch_dict()
        data["profile"] = {"user": "virtual"}
        path = self.write_raw("unknown-profile.json", json.dumps(data))
        with self.assertRaises(RuleReviewFileError) as ctx:
            load_rule_batch(path)
        self.assertEqual(str(ctx.exception), "invalid_rule_review_file")

    def test_rejects_unknown_nested_field(self) -> None:
        data = self.valid_batch_dict()
        docs = data["documents"]
        assert isinstance(docs, list)
        first = docs[0]
        assert isinstance(first, dict)
        first["extra_field"] = "virtual"
        path = self.write_raw("unknown-nested.json", json.dumps(data))
        with self.assertRaises(RuleReviewFileError) as ctx:
            load_rule_batch(path)
        self.assertEqual(str(ctx.exception), "invalid_rule_review_file")

    def test_rejects_wrong_type_official_string(self) -> None:
        data = self.valid_batch_dict()
        docs = data["documents"]
        assert isinstance(docs, list)
        first = docs[0]
        assert isinstance(first, dict)
        first["official"] = "true"
        path = self.write_raw("wrong-type.json", json.dumps(data))
        with self.assertRaises(RuleReviewFileError) as ctx:
            load_rule_batch(path)
        self.assertEqual(str(ctx.exception), "invalid_rule_review_file")

    def test_rejects_bool_as_version(self) -> None:
        data = self.valid_batch_dict()
        data["format_version"] = True
        path = self.write_raw("bool-version.json", json.dumps(data))
        with self.assertRaises(RuleReviewFileError) as ctx:
            load_rule_batch(path)
        self.assertEqual(str(ctx.exception), "invalid_rule_review_file")

    def test_rejects_unknown_enum_kind(self) -> None:
        data = self.valid_batch_dict()
        cands = data["candidates"]
        assert isinstance(cands, list)
        first = cands[0]
        assert isinstance(first, dict)
        first["kind"] = "bogus-kind"
        path = self.write_raw("unknown-enum.json", json.dumps(data))
        with self.assertRaises(RuleReviewFileError) as ctx:
            load_rule_batch(path)
        self.assertEqual(str(ctx.exception), "invalid_rule_review_file")

    def test_rejects_duplicate_keys(self) -> None:
        raw = '{"format_version": 1, "format_version": 1, "documents": [], "policies": [], "candidates": []}'
        path = self.write_raw("duplicate.json", raw)
        with self.assertRaises(RuleReviewFileError) as ctx:
            load_rule_batch(path)
        self.assertEqual(str(ctx.exception), "invalid_rule_review_file")

    def test_rejects_nan_constant(self) -> None:
        raw = '{"format_version": 1, "documents": NaN, "policies": [], "candidates": []}'
        path = self.write_raw("nan.json", raw)
        with self.assertRaises(RuleReviewFileError) as ctx:
            load_rule_batch(path)
        self.assertEqual(str(ctx.exception), "invalid_rule_review_file")

    def test_rejects_naive_timestamp(self) -> None:
        data = self.valid_batch_dict()
        docs = data["documents"]
        assert isinstance(docs, list)
        first = docs[0]
        assert isinstance(first, dict)
        first["collected_at"] = "2026-10-20T00:00:00"
        path = self.write_raw("naive-time.json", json.dumps(data))
        with self.assertRaises(RuleReviewFileError) as ctx:
            load_rule_batch(path)
        self.assertEqual(str(ctx.exception), "invalid_rule_review_file")

    def test_rejects_unsupported_version(self) -> None:
        data = self.valid_batch_dict()
        data["format_version"] = 2
        path = self.write_raw("version2.json", json.dumps(data))
        with self.assertRaises(RuleReviewFileError) as ctx:
            load_rule_batch(path)
        self.assertEqual(str(ctx.exception), "unsupported_rule_review_file_version")

    def test_rejects_invalid_digest_format(self) -> None:
        snapshot = make_snapshot(approved=True)
        path = self.root / "good-snapshot.json"
        write_rule_snapshot(path, snapshot)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        reviews = data["reviews"]
        assert isinstance(reviews, list)
        first = reviews[0]
        assert isinstance(first, dict)
        approval = first["approval"]
        assert isinstance(approval, dict)
        approval["candidate_sha256"] = "short"
        bad = self.write_raw("bad-digest.json", json.dumps(data))
        with self.assertRaises(RuleReviewFileError) as ctx:
            load_rule_snapshot(bad)
        self.assertEqual(str(ctx.exception), "invalid_rule_review_file")

    def test_rejects_blank_reviewer(self) -> None:
        snapshot = make_snapshot(approved=True)
        path = self.root / "good2.json"
        write_rule_snapshot(path, snapshot)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        reviews = data["reviews"]
        assert isinstance(reviews, list)
        first = reviews[0]
        assert isinstance(first, dict)
        approval = first["approval"]
        assert isinstance(approval, dict)
        approval["reviewer"] = "   "
        bad = self.write_raw("blank-reviewer.json", json.dumps(data))
        with self.assertRaises(RuleReviewFileError) as ctx:
            load_rule_snapshot(bad)
        self.assertEqual(str(ctx.exception), "invalid_rule_review_file")

    def test_rejects_lone_surrogate_text(self) -> None:
        data = self.valid_batch_dict()
        raw_text = json.dumps(data, ensure_ascii=False)
        escape = chr(92) + "ud800"
        marker = QUOTE
        assert marker in raw_text
        corrupted = raw_text.replace(marker, escape, 1)
        path = self.write_raw("lone-surrogate.json", corrupted)
        with self.assertRaises(RuleReviewFileError) as ctx:
            load_rule_batch(path)
        self.assertEqual(str(ctx.exception), "invalid_rule_review_file")


class ErrorHygieneTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)

    def test_error_messages_hide_details(self) -> None:
        secret = "secret-virtual-xyz-001"
        path = self.root / secret / "bad.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{bad json " + secret, encoding="utf-8")
        allowed = {
            "invalid_rule_review_file",
            "unsupported_rule_review_file_version",
            "rule_review_output_exists",
            "rule_review_io_error",
        }
        try:
            load_rule_batch(path)
            self.fail("expected RuleReviewFileError")
        except RuleReviewFileError as exc:
            message = str(exc)
            self.assertIn(message, allowed)
            self.assertNotIn(secret, message)
            self.assertNotIn("bad json", message)
            self.assertNotIn(str(path), message)
            self.assertIsNone(exc.__cause__)

    def test_load_missing_file_is_io_error(self) -> None:
        path = self.root / "no-such-file.json"
        with self.assertRaises(RuleReviewFileError) as ctx:
            load_rule_batch(path)
        self.assertEqual(str(ctx.exception), "rule_review_io_error")


class CreateOnlyWriteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)

    def test_write_creates_parents_and_mode_600(self) -> None:
        batch = make_batch()
        path = self.root / "a" / "b" / "batch.json"
        write_rule_batch(path, batch)
        self.assertTrue(path.is_file())
        mode = stat.S_IMODE(os.stat(path).st_mode)
        self.assertEqual(mode, 0o600)

    def test_existing_file_preserved(self) -> None:
        first = make_batch()
        path = self.root / "existing.json"
        write_rule_batch(path, first)
        before = path.read_bytes()
        second = RuleReviewBatch(documents=(), policies=(), candidates=())
        with self.assertRaises(RuleReviewFileError) as ctx:
            write_rule_batch(path, second)
        self.assertEqual(str(ctx.exception), "rule_review_output_exists")
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(load_rule_batch(path), first)

    def test_link_failure_preserves_success_and_cleans_temp(self) -> None:
        import unittest.mock as mock

        batch = make_batch()
        good_path = self.root / "good.json"
        write_rule_batch(good_path, batch)
        good_bytes = good_path.read_bytes()
        target = self.root / "new-fail.json"
        with mock.patch("os.link", side_effect=OSError("virtual link unavailable")):
            with self.assertRaises(RuleReviewFileError) as ctx:
                write_rule_batch(target, batch)
            self.assertEqual(str(ctx.exception), "rule_review_io_error")
        self.assertFalse(target.exists())
        self.assertEqual(good_path.read_bytes(), good_bytes)
        leftovers = [p for p in self.root.iterdir() if p.name.startswith(".rule-review-")]
        self.assertEqual(leftovers, [])

    def test_concurrent_same_target_one_wins(self) -> None:
        batch_a = make_batch()
        doc = make_document("doc-virtual-01")
        batch_b = RuleReviewBatch(
            documents=(doc,),
            policies=(make_policy(EMPLOYMENT_ID),),
            candidates=(),
        )
        target = self.root / "race.json"
        errors: list[str] = []
        barrier = threading.Barrier(2)

        def attempt(batch: RuleReviewBatch) -> None:
            try:
                barrier.wait(timeout=5)
                write_rule_batch(target, batch)
            except RuleReviewFileError as exc:
                errors.append(str(exc))
            except Exception as exc:
                errors.append("unexpected:" + exc.__class__.__name__)

        thread_a = threading.Thread(target=attempt, args=(batch_a,))
        thread_b = threading.Thread(target=attempt, args=(batch_b,))
        thread_a.start()
        thread_b.start()
        thread_a.join(timeout=10)
        thread_b.join(timeout=10)
        self.assertFalse(thread_a.is_alive())
        self.assertFalse(thread_b.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0], "rule_review_output_exists")
        loaded = load_rule_batch(target)
        self.assertIn(loaded, (batch_a, batch_b))

    def test_sync_failure_leaves_no_partial_result_or_temporary_file(self) -> None:
        import unittest.mock as mock

        target = self.root / "failed.json"
        with mock.patch("os.fsync", side_effect=OSError("virtual sync failure")):
            with self.assertRaisesRegex(RuleReviewFileError, "^rule_review_io_error$"):
                write_rule_batch(target, make_batch())
        self.assertFalse(target.exists())
        self.assertEqual(list(self.root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
