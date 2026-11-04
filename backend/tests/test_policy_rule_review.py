"""Policy rule validation and human review regressions with synthetic documents.

Only virtual documents and example.invalid URLs are used. No real
collection data, .env, credentials, or network access.
"""

from __future__ import annotations

import dataclasses
import unittest
from datetime import datetime, timedelta, timezone

from benefitfinder.application.policy_rules import (
    CandidateReview,
    PolicyReference,
    ReviewIssue,
    RuleCandidate,
    RuleCitation,
    RuleDirection,
    RuleKind,
    SourceDocument,
)
from benefitfinder.ingestion.policy_rule_review import (
    approve_candidate,
    prepare_rule_reviews,
    refresh_review,
    validate_candidate,
)

UTC = timezone.utc
BASE_COLLECTED = datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
REVIEWED_AT = datetime(2026, 10, 27, 0, 0, tzinfo=UTC)
QUOTE = "TEXT_QUOTE_VIRTUAL_001"

EMPLOYMENT_ID = "youth-employment-virtual-01"
HOUSING_ID = "youth-housing-virtual-02"


def make_policy(
    policy_id: str = EMPLOYMENT_ID,
    categories: tuple[str, ...] = ("employment",),
    domain: str = "youth",
) -> PolicyReference:
    return PolicyReference(id=policy_id, domain=domain, categories=categories)


def make_document(
    doc_id: str = "doc-employment-01",
    policy_id: str = EMPLOYMENT_ID,
    text: str | None = None,
    version: str = "v2026-01",
    collected_at: datetime = BASE_COLLECTED,
    official: bool = True,
    url: str | None = None,
) -> SourceDocument:
    body = text if text is not None else "virtual official notice: " + QUOTE + "."
    return SourceDocument(
        id=doc_id,
        policy_id=policy_id,
        source_url=url or ("https://example.invalid/docs/" + doc_id),
        document_version=version,
        collected_at=collected_at,
        text=body,
        official=official,
    )


def citation_for(
    document: SourceDocument, quote: str = QUOTE, location: str = "section 3"
) -> RuleCitation:
    return RuleCitation(
        document_id=document.id,
        document_version=document.document_version,
        content_sha256=document.content_sha256,
        quote=quote,
        location=location,
    )


def make_candidate(
    candidate_id: str = "candidate-virtual-01",
    source_policy_id: str = EMPLOYMENT_ID,
    target_policy_ids: tuple[str, ...] = (HOUSING_ID,),
    kind: RuleKind | None = None,
    direction: RuleDirection | None = None,
    description: str = "virtual rule: the two virtual policies cannot be received together",
    citations: tuple[RuleCitation, ...] | None = None,
    conditions: tuple[str, ...] = (),
    exceptions: tuple[str, ...] = (),
    period: str | None = None,
    amount_effect: str | None = None,
    unresolved: tuple[str, ...] = (),
) -> RuleCandidate:
    return RuleCandidate(
        id=candidate_id,
        source_policy_id=source_policy_id,
        target_policy_ids=target_policy_ids,
        kind=kind if kind is not None else RuleKind.SIMULTANEOUS_EXCLUSION,
        description=description,
        citations=citations if citations is not None else (),
        direction=direction if direction is not None else RuleDirection.BOTH,
        conditions=conditions,
        exceptions=exceptions,
        period=period,
        amount_effect=amount_effect,
        unresolved=unresolved,
    )


def valid_fixture() -> tuple[
    tuple[SourceDocument, ...], tuple[PolicyReference, ...], RuleCandidate
]:
    employment = make_policy(EMPLOYMENT_ID, ("employment",))
    housing = make_policy(HOUSING_ID, ("housing",))
    source_doc = make_document(
        "doc-employment-01",
        EMPLOYMENT_ID,
        text="virtual notice (employment): " + QUOTE + ".",
    )
    target_doc = make_document(
        "doc-housing-01",
        HOUSING_ID,
        text="virtual notice (housing): " + QUOTE + ".",
    )
    documents = (source_doc, target_doc)
    policies = (employment, housing)
    candidate = make_candidate(
        citations=(citation_for(source_doc), citation_for(target_doc, location="section 4")),
    )
    return documents, policies, candidate


def codes(issues: tuple[ReviewIssue, ...]) -> set[str]:
    return {issue.code for issue in issues}


class StaticExtractor:
    def __init__(self, candidates: tuple[RuleCandidate, ...]) -> None:
        self.candidates = candidates
        self.calls = []

    def extract(self, documents: tuple[SourceDocument, ...]) -> tuple[RuleCandidate, ...]:
        self.calls.append(documents)
        return self.candidates


class RaisingExtractor:
    def __init__(self, message: str = "secret-provider-boom") -> None:
        self.message = message
        self.calls = 0

    def extract(self, documents: tuple[SourceDocument, ...]) -> tuple[RuleCandidate, ...]:
        self.calls += 1
        raise RuntimeError(self.message)


class HappyPathTest(unittest.TestCase):
    def test_valid_candidate_has_no_issues(self) -> None:
        documents, policies, candidate = valid_fixture()
        self.assertEqual(validate_candidate(candidate, documents, policies), ())

    def test_prepare_keeps_verified_candidate_in_needs_review(self) -> None:
        documents, policies, candidate = valid_fixture()
        result = prepare_rule_reviews(documents, policies, StaticExtractor((candidate,)))
        self.assertIsNone(result.error_code)
        self.assertEqual(len(result.reviews), 1)
        review = result.reviews[0]
        self.assertEqual(review.candidate, candidate)
        self.assertEqual(review.issues, ())
        self.assertIsNone(review.approval)
        self.assertEqual(review.status, "needs_review")

    def test_approve_valid_review_records_binding(self) -> None:
        documents, policies, candidate = valid_fixture()
        review = CandidateReview(candidate, validate_candidate(candidate, documents, policies))
        approved = approve_candidate(
            review,
            documents,
            policies,
            candidates=(candidate,),
            reviewer="seo-yun",
            reviewed_at=REVIEWED_AT,
        )
        self.assertEqual(approved.candidate, candidate)
        self.assertEqual(approved.issues, ())
        self.assertIsNotNone(approved.approval)
        self.assertEqual(approved.status, "approved")
        self.assertTrue(approved.approval.candidate_sha256)
        self.assertTrue(approved.approval.documents_sha256)
        self.assertEqual(approved.approval.reviewer, "seo-yun")
        self.assertEqual(approved.approval.reviewed_at, REVIEWED_AT)

    def test_approve_trims_reviewer_name(self) -> None:
        documents, policies, candidate = valid_fixture()
        review = CandidateReview(candidate, validate_candidate(candidate, documents, policies))
        approved = approve_candidate(
            review,
            documents,
            policies,
            candidates=(candidate,),
            reviewer="  seo-yun  ",
            reviewed_at=REVIEWED_AT,
        )
        self.assertEqual(approved.approval.reviewer, "seo-yun")


class EvidenceTest(unittest.TestCase):
    def test_missing_citations_requires_review(self) -> None:
        documents, policies, candidate = valid_fixture()
        naked = dataclasses.replace(candidate, citations=())
        issues = validate_candidate(naked, documents, policies)
        self.assertTrue(issues)
        with self.assertRaises(ValueError) as ctx:
            approve_candidate(
                CandidateReview(naked, issues),
                documents,
                policies,
                candidates=(naked,),
                reviewer="seo-yun",
                reviewed_at=REVIEWED_AT,
            )
        self.assertEqual(str(ctx.exception), "candidate_not_approvable")

    def test_empty_quote_requires_review(self) -> None:
        documents, policies, candidate = valid_fixture()
        source_doc = documents[0]
        bad_citation = RuleCitation(
            document_id=source_doc.id,
            document_version=source_doc.document_version,
            content_sha256=source_doc.content_sha256,
            quote="   ",
            location="section 3",
        )
        bad = dataclasses.replace(candidate, citations=(bad_citation,))
        self.assertTrue(validate_candidate(bad, documents, policies))

    def test_wrong_quote_requires_review(self) -> None:
        documents, policies, candidate = valid_fixture()
        source_doc, target_doc = documents
        bad = dataclasses.replace(
            candidate,
            citations=(
                citation_for(source_doc, quote="never-appears-virtual-xyz"),
                citation_for(target_doc, location="section 4"),
            ),
        )
        issues = validate_candidate(bad, documents, policies)
        self.assertTrue(issues)
        self.assertIn("quote_not_found", codes(issues))

    def test_stale_version_requires_review(self) -> None:
        documents, policies, candidate = valid_fixture()
        source_doc, target_doc = documents
        stale = dataclasses.replace(
            candidate,
            citations=(
                RuleCitation(
                    document_id=source_doc.id,
                    document_version="v1999-99",
                    content_sha256=source_doc.content_sha256,
                    quote=QUOTE,
                    location="section 3",
                ),
                citation_for(target_doc, location="section 4"),
            ),
        )
        issues = validate_candidate(stale, documents, policies)
        self.assertIn("stale_evidence", codes(issues))

    def test_stale_hash_requires_review(self) -> None:
        documents, policies, candidate = valid_fixture()
        source_doc, target_doc = documents
        stale = dataclasses.replace(
            candidate,
            citations=(
                RuleCitation(
                    document_id=source_doc.id,
                    document_version=source_doc.document_version,
                    content_sha256="0" * 64,
                    quote=QUOTE,
                    location="section 3",
                ),
                citation_for(target_doc, location="section 4"),
            ),
        )
        self.assertIn("stale_evidence", codes(validate_candidate(stale, documents, policies)))

    def test_missing_location_requires_review(self) -> None:
        documents, policies, candidate = valid_fixture()
        source_doc, target_doc = documents
        bad = dataclasses.replace(
            candidate,
            citations=(
                citation_for(source_doc, location="  "),
                citation_for(target_doc, location="section 4"),
            ),
        )
        issues = validate_candidate(bad, documents, policies)
        self.assertIn("missing_evidence_location", codes(issues))

    def test_missing_source_evidence_requires_review(self) -> None:
        documents, policies, candidate = valid_fixture()
        target_doc = documents[1]
        only_target = dataclasses.replace(
            candidate, citations=(citation_for(target_doc, location="section 4"),)
        )
        issues = validate_candidate(only_target, documents, policies)
        self.assertIn("missing_source_evidence", codes(issues))

    def test_missing_document_requires_review(self) -> None:
        documents, policies, candidate = valid_fixture()
        ghost = RuleCitation(
            document_id="doc-ghost-99",
            document_version="v2026-01",
            content_sha256="a" * 64,
            quote=QUOTE,
            location="section 9",
        )
        bad = dataclasses.replace(candidate, citations=(ghost,))
        issues = validate_candidate(bad, documents, policies)
        self.assertIn("missing_document", codes(issues))

    def test_unrelated_document_requires_review(self) -> None:
        third_policy = make_policy("youth-employment-virtual-03", ("employment",))
        policies = (make_policy(), make_policy(HOUSING_ID, ("housing",)), third_policy)
        third_doc = make_document("doc-extra-03", third_policy.id)
        documents, _, candidate = valid_fixture()
        documents = (documents[0], documents[1], third_doc)
        bad = dataclasses.replace(
            candidate,
            citations=(
                citation_for(documents[0]),
                RuleCitation(
                    document_id=third_doc.id,
                    document_version=third_doc.document_version,
                    content_sha256=third_doc.content_sha256,
                    quote=QUOTE,
                    location="appendix",
                ),
            ),
        )
        self.assertTrue(validate_candidate(bad, documents, policies))


class PolicyScopeTest(unittest.TestCase):
    def test_unknown_policy_requires_review(self) -> None:
        documents, policies, candidate = valid_fixture()
        bad = dataclasses.replace(candidate, target_policy_ids=("youth-housing-unknown-99",))
        self.assertTrue(validate_candidate(bad, documents, policies))

    def test_out_of_scope_category_requires_review(self) -> None:
        documents, policies, _ = valid_fixture()
        other = make_policy("youth-education-virtual-09", ("education",))
        scoped_policies = (policies[0], policies[1], other)
        candidate = make_candidate(
            target_policy_ids=(HOUSING_ID, other.id),
            citations=(
                citation_for(documents[0]),
                citation_for(documents[1], location="section 4"),
            ),
        )
        issues = validate_candidate(candidate, documents, scoped_policies)
        self.assertIn("out_of_scope_policy", codes(issues))

    def test_composite_with_extra_category_requires_review(self) -> None:
        documents, policies, _ = valid_fixture()
        composite = make_policy(
            "youth-composite-virtual-04", ("employment", "housing", "education")
        )
        scoped_policies = (policies[0], policies[1], composite)
        candidate = make_candidate(
            target_policy_ids=(HOUSING_ID, composite.id),
            citations=(
                citation_for(documents[0]),
                citation_for(documents[1], location="section 4"),
            ),
        )
        issues = validate_candidate(candidate, documents, scoped_policies)
        self.assertIn("out_of_scope_policy", codes(issues))

    def test_composite_employment_housing_is_in_scope(self) -> None:
        employment = make_policy(EMPLOYMENT_ID, ("employment",))
        composite = make_policy("youth-composite-virtual-05", ("employment", "housing"))
        composite_doc = make_document("doc-composite-05", composite.id)
        source_doc = make_document("doc-employment-01", EMPLOYMENT_ID)
        docs = (source_doc, composite_doc)
        policies = (employment, composite)
        candidate = make_candidate(
            source_policy_id=EMPLOYMENT_ID,
            target_policy_ids=(composite.id,),
            citations=(citation_for(source_doc), citation_for(composite_doc, location="section 4")),
        )
        self.assertEqual(validate_candidate(candidate, docs, policies), ())

    def test_missing_target_self_and_duplicate_require_review(self) -> None:
        documents, policies, candidate = valid_fixture()
        no_target = dataclasses.replace(candidate, target_policy_ids=())
        self.assertTrue(validate_candidate(no_target, documents, policies))
        self_target = dataclasses.replace(candidate, target_policy_ids=(EMPLOYMENT_ID,))
        self.assertTrue(validate_candidate(self_target, documents, policies))
        dup_target = dataclasses.replace(candidate, target_policy_ids=(HOUSING_ID, HOUSING_ID))
        self.assertTrue(validate_candidate(dup_target, documents, policies))

    def test_blank_candidate_fields_require_review(self) -> None:
        documents, policies, candidate = valid_fixture()
        blank_id = dataclasses.replace(candidate, id="   ")
        self.assertTrue(validate_candidate(blank_id, documents, policies))
        blank_desc = dataclasses.replace(candidate, description="  ")
        self.assertTrue(validate_candidate(blank_desc, documents, policies))


class ComplexityHoldTest(unittest.TestCase):
    def check_hold(self, candidate: RuleCandidate) -> None:
        documents, policies, _ = valid_fixture()
        source_doc, target_doc = documents
        rebuilt = dataclasses.replace(
            candidate,
            source_policy_id=EMPLOYMENT_ID,
            target_policy_ids=(HOUSING_ID,),
            citations=(citation_for(source_doc), citation_for(target_doc, location="section 4")),
        )
        issues = validate_candidate(rebuilt, documents, policies)
        self.assertTrue(issues)
        review = CandidateReview(rebuilt, issues)
        self.assertEqual(review.status, "needs_review")
        self.assertEqual(review.candidate, rebuilt)
        with self.assertRaises(ValueError) as ctx:
            approve_candidate(
                review,
                documents,
                policies,
                candidates=(rebuilt,),
                reviewer="seo-yun",
                reviewed_at=REVIEWED_AT,
            )
        self.assertEqual(str(ctx.exception), "candidate_not_approvable")

    def test_conditional_kind_holds(self) -> None:
        _, _, base = valid_fixture()
        self.check_hold(dataclasses.replace(base, kind=RuleKind.CONDITIONAL))

    def test_history_kind_holds(self) -> None:
        _, _, base = valid_fixture()
        self.check_hold(dataclasses.replace(base, kind=RuleKind.HISTORY))

    def test_deduction_kind_holds(self) -> None:
        _, _, base = valid_fixture()
        self.check_hold(dataclasses.replace(base, kind=RuleKind.DEDUCTION))

    def test_cap_kind_holds(self) -> None:
        _, _, base = valid_fixture()
        self.check_hold(dataclasses.replace(base, kind=RuleKind.CAP))

    def test_unknown_kind_holds(self) -> None:
        _, _, base = valid_fixture()
        self.check_hold(dataclasses.replace(base, kind=RuleKind.UNKNOWN))

    def test_one_way_direction_holds(self) -> None:
        _, _, base = valid_fixture()
        self.check_hold(dataclasses.replace(base, direction=RuleDirection.SOURCE_TO_TARGET))

    def test_unknown_direction_holds(self) -> None:
        _, _, base = valid_fixture()
        self.check_hold(dataclasses.replace(base, direction=RuleDirection.UNKNOWN))

    def test_conditions_hold(self) -> None:
        _, _, base = valid_fixture()
        self.check_hold(dataclasses.replace(base, conditions=("virtual condition",)))

    def test_exceptions_hold(self) -> None:
        _, _, base = valid_fixture()
        self.check_hold(dataclasses.replace(base, exceptions=("virtual exception",)))

    def test_period_holds(self) -> None:
        _, _, base = valid_fixture()
        self.check_hold(dataclasses.replace(base, period="virtual period 2026"))

    def test_amount_effect_holds(self) -> None:
        _, _, base = valid_fixture()
        self.check_hold(dataclasses.replace(base, amount_effect="virtual deduction"))

    def test_unresolved_alias_holds(self) -> None:
        _, _, base = valid_fixture()
        self.check_hold(dataclasses.replace(base, unresolved=("virtual alias unresolved",)))


class PrepareBatchTest(unittest.TestCase):
    def test_empty_extraction_returns_no_reviews(self) -> None:
        documents, policies, _ = valid_fixture()
        result = prepare_rule_reviews(documents, policies, StaticExtractor(()))
        self.assertIsNone(result.error_code)
        self.assertEqual(result.reviews, ())

    def test_conflicting_pair_holds_all_candidates(self) -> None:
        documents, policies, base = valid_fixture()
        source_doc, target_doc = documents
        first = dataclasses.replace(
            base,
            id="candidate-virtual-a",
            description="virtual reading A",
            citations=(citation_for(source_doc), citation_for(target_doc, location="section 4")),
        )
        second = dataclasses.replace(
            base,
            id="candidate-virtual-b",
            description="virtual reading B",
            kind=RuleKind.CONDITIONAL,
            citations=(citation_for(source_doc), citation_for(target_doc, location="section 4")),
        )
        result = prepare_rule_reviews(documents, policies, StaticExtractor((first, second)))
        self.assertIsNone(result.error_code)
        self.assertEqual(len(result.reviews), 2)
        for review in result.reviews:
            self.assertIn("conflicting_candidates", codes(review.issues))
            self.assertEqual(review.status, "needs_review")
            self.assertIsNone(review.approval)

    def test_different_pairs_do_not_conflict(self) -> None:
        employment = make_policy(EMPLOYMENT_ID, ("employment",))
        housing = make_policy(HOUSING_ID, ("housing",))
        third = make_policy("youth-employment-virtual-03", ("employment",))
        source_doc = make_document("doc-employment-01", EMPLOYMENT_ID)
        housing_doc = make_document("doc-housing-01", HOUSING_ID)
        third_doc = make_document("doc-employment-03", third.id)
        documents = (source_doc, housing_doc, third_doc)
        policies = (employment, housing, third)
        first = make_candidate(
            candidate_id="candidate-virtual-a",
            source_policy_id=EMPLOYMENT_ID,
            target_policy_ids=(HOUSING_ID,),
            citations=(citation_for(source_doc), citation_for(housing_doc, location="section 4")),
        )
        second = make_candidate(
            candidate_id="candidate-virtual-b",
            source_policy_id=EMPLOYMENT_ID,
            target_policy_ids=(third.id,),
            citations=(citation_for(source_doc), citation_for(third_doc, location="section 5")),
        )
        result = prepare_rule_reviews(documents, policies, StaticExtractor((first, second)))
        self.assertIsNone(result.error_code)
        for review in result.reviews:
            self.assertNotIn("conflicting_candidates", codes(review.issues))

    def test_duplicate_candidate_id_holds(self) -> None:
        documents, policies, base = valid_fixture()
        source_doc, target_doc = documents
        other_policy = make_policy("youth-employment-virtual-03", ("employment",))
        other_doc = make_document("doc-employment-03", other_policy.id)
        policies2 = (policies[0], policies[1], other_policy)
        documents2 = (source_doc, target_doc, other_doc)
        first = dataclasses.replace(
            base,
            id="candidate-duplicate-01",
            citations=(citation_for(source_doc), citation_for(target_doc, location="section 4")),
        )
        second = make_candidate(
            candidate_id="candidate-duplicate-01",
            source_policy_id=EMPLOYMENT_ID,
            target_policy_ids=(other_policy.id,),
            citations=(citation_for(source_doc), citation_for(other_doc, location="section 5")),
        )
        result = prepare_rule_reviews(documents2, policies2, StaticExtractor((first, second)))
        self.assertIsNone(result.error_code)
        for review in result.reviews:
            self.assertIn("duplicate_candidate_id", codes(review.issues))
            self.assertEqual(review.status, "needs_review")

    def test_invalid_input_duplicate_document_id(self) -> None:
        documents, policies, _ = valid_fixture()
        dup = (documents[0], dataclasses.replace(documents[0], policy_id=HOUSING_ID))
        extractor = StaticExtractor(())
        result = prepare_rule_reviews(dup, policies, extractor)
        self.assertEqual(result.error_code, "invalid_input")
        self.assertEqual(result.reviews, ())
        self.assertEqual(extractor.calls, [])

    def test_invalid_input_duplicate_policy_id(self) -> None:
        documents, policies, _ = valid_fixture()
        dup_policies = (policies[0], make_policy(EMPLOYMENT_ID, ("employment",)))
        extractor = StaticExtractor(())
        result = prepare_rule_reviews(documents, dup_policies, extractor)
        self.assertEqual(result.error_code, "invalid_input")
        self.assertEqual(extractor.calls, [])

    def test_invalid_input_unofficial_document(self) -> None:
        documents, policies, _ = valid_fixture()
        unofficial = (dataclasses.replace(documents[0], official=False), documents[1])
        extractor = StaticExtractor(())
        result = prepare_rule_reviews(unofficial, policies, extractor)
        self.assertEqual(result.error_code, "invalid_input")
        self.assertEqual(extractor.calls, [])

    def test_invalid_input_unknown_source_document(self) -> None:
        documents, policies, _ = valid_fixture()
        stray = make_document("doc-stray-99", "youth-housing-unknown-99")
        extractor = StaticExtractor(())
        result = prepare_rule_reviews((documents[0], stray), policies, extractor)
        self.assertEqual(result.error_code, "invalid_input")
        self.assertEqual(extractor.calls, [])

    def test_invalid_input_empty_documents(self) -> None:
        _, policies, _ = valid_fixture()
        extractor = StaticExtractor(())
        result = prepare_rule_reviews((), policies, extractor)
        self.assertEqual(result.error_code, "invalid_input")
        self.assertEqual(extractor.calls, [])

    def test_invalid_input_non_tuple_documents(self) -> None:
        documents, policies, _ = valid_fixture()
        extractor = StaticExtractor(())
        result = prepare_rule_reviews(list(documents), policies, extractor)
        self.assertEqual(result.error_code, "invalid_input")
        self.assertEqual(extractor.calls, [])

    def test_invalid_input_non_tuple_policies(self) -> None:
        documents, policies, _ = valid_fixture()
        extractor = StaticExtractor(())
        result = prepare_rule_reviews(documents, list(policies), extractor)
        self.assertEqual(result.error_code, "invalid_input")
        self.assertEqual(extractor.calls, [])

    def test_invalid_input_blank_policy_id(self) -> None:
        documents, policies, _ = valid_fixture()
        bad_policies = (make_policy("   ", ("employment",)), policies[1])
        extractor = StaticExtractor(())
        result = prepare_rule_reviews(documents, bad_policies, extractor)
        self.assertEqual(result.error_code, "invalid_input")
        self.assertEqual(extractor.calls, [])

    def test_invalid_input_bad_categories_type(self) -> None:
        documents, policies, _ = valid_fixture()
        bad = PolicyReference(id=EMPLOYMENT_ID, domain="youth", categories=["employment"])
        extractor = StaticExtractor(())
        result = prepare_rule_reviews(documents, (bad, policies[1]), extractor)
        self.assertEqual(result.error_code, "invalid_input")
        self.assertEqual(extractor.calls, [])

    def test_extraction_failure_hides_provider_message(self) -> None:
        documents, policies, _ = valid_fixture()
        secret = "secret-provider-boom-virtual-000"
        result = prepare_rule_reviews(documents, policies, RaisingExtractor(secret))
        self.assertEqual(result.error_code, "extraction_failed")
        self.assertEqual(result.reviews, ())
        self.assertNotIn(secret, repr(result))

    def test_invalid_extraction_result_for_wrong_type(self) -> None:
        documents, policies, _ = valid_fixture()

        class ListExtractor:
            def extract(self, docs: tuple[SourceDocument, ...]) -> object:
                docs2, _, cand = valid_fixture()
                return [cand]

        result = prepare_rule_reviews(documents, policies, ListExtractor())
        self.assertEqual(result.error_code, "invalid_extraction_result")

    def test_invalid_extraction_result_for_bad_item(self) -> None:
        documents, policies, _ = valid_fixture()

        class BadItemExtractor:
            def extract(self, docs: tuple[SourceDocument, ...]) -> object:
                return ("not-a-candidate",)

        result = prepare_rule_reviews(documents, policies, BadItemExtractor())
        self.assertEqual(result.error_code, "invalid_extraction_result")


class ApproveReviewTest(unittest.TestCase):
    def test_approve_rejects_batch_issues_without_bypass(self) -> None:
        documents, policies, candidate = valid_fixture()
        tainted = CandidateReview(candidate, (ReviewIssue("conflicting_candidates"),))
        with self.assertRaises(ValueError) as ctx:
            approve_candidate(
                tainted,
                documents,
                policies,
                candidates=(candidate,),
                reviewer="seo-yun",
                reviewed_at=REVIEWED_AT,
            )
        self.assertEqual(str(ctx.exception), "candidate_not_approvable")

    def test_approve_rejects_repackaged_review_when_batch_conflicts(self) -> None:
        documents, policies, base = valid_fixture()
        source_doc, target_doc = documents
        first = dataclasses.replace(base, id="candidate-virtual-a", description="virtual A")
        second = dataclasses.replace(base, id="candidate-virtual-b", description="virtual B")
        both = (first, second)
        repackaged = CandidateReview(first, ())
        with self.assertRaises(ValueError) as ctx:
            approve_candidate(
                repackaged,
                documents,
                policies,
                candidates=both,
                reviewer="seo-yun",
                reviewed_at=REVIEWED_AT,
            )
        self.assertEqual(str(ctx.exception), "candidate_not_approvable")

    def test_approve_rejects_duplicate_id_review(self) -> None:
        documents, policies, candidate = valid_fixture()
        tainted = CandidateReview(candidate, (ReviewIssue("duplicate_candidate_id"),))
        with self.assertRaises(ValueError) as ctx:
            approve_candidate(
                tainted,
                documents,
                policies,
                candidates=(candidate,),
                reviewer="seo-yun",
                reviewed_at=REVIEWED_AT,
            )
        self.assertEqual(str(ctx.exception), "candidate_not_approvable")

    def test_approve_rejects_candidate_missing_from_batch(self) -> None:
        documents, policies, candidate = valid_fixture()
        _, _, other_base = valid_fixture()
        other = dataclasses.replace(other_base, id="candidate-virtual-other")
        review = CandidateReview(candidate, validate_candidate(candidate, documents, policies))
        self.assertEqual(review.issues, ())
        with self.assertRaises(ValueError) as ctx:
            approve_candidate(
                review,
                documents,
                policies,
                candidates=(other,),
                reviewer="seo-yun",
                reviewed_at=REVIEWED_AT,
            )
        self.assertEqual(str(ctx.exception), "candidate_not_approvable")

    def test_approve_rejects_non_tuple_candidates(self) -> None:
        documents, policies, candidate = valid_fixture()
        review = CandidateReview(candidate, validate_candidate(candidate, documents, policies))
        with self.assertRaises(ValueError) as ctx:
            approve_candidate(
                review,
                documents,
                policies,
                candidates=[candidate],
                reviewer="seo-yun",
                reviewed_at=REVIEWED_AT,
            )
        self.assertEqual(str(ctx.exception), "candidate_not_approvable")

    def test_approve_rejects_blank_reviewer(self) -> None:
        documents, policies, candidate = valid_fixture()
        review = CandidateReview(candidate, validate_candidate(candidate, documents, policies))
        for bad_reviewer in ("", "   "):
            with self.assertRaises(ValueError) as ctx:
                approve_candidate(
                    review,
                    documents,
                    policies,
                    candidates=(candidate,),
                    reviewer=bad_reviewer,
                    reviewed_at=REVIEWED_AT,
                )
            self.assertEqual(str(ctx.exception), "reviewer_required")

    def test_approve_rejects_naive_review_time(self) -> None:
        documents, policies, candidate = valid_fixture()
        review = CandidateReview(candidate, validate_candidate(candidate, documents, policies))
        with self.assertRaises(ValueError) as ctx:
            approve_candidate(
                review,
                documents,
                policies,
                candidates=(candidate,),
                reviewer="seo-yun",
                reviewed_at=datetime(2026, 10, 27, 0, 0),
            )
        self.assertEqual(str(ctx.exception), "review_time_requires_timezone")

    def test_approve_rejects_review_before_collection(self) -> None:
        documents, policies, candidate = valid_fixture()
        review = CandidateReview(candidate, validate_candidate(candidate, documents, policies))
        too_early = BASE_COLLECTED - timedelta(seconds=1)
        with self.assertRaises(ValueError) as ctx:
            approve_candidate(
                review,
                documents,
                policies,
                candidates=(candidate,),
                reviewer="seo-yun",
                reviewed_at=too_early,
            )
        self.assertEqual(str(ctx.exception), "review_precedes_collection")

    def test_approve_rejects_unsupported_candidate(self) -> None:
        documents, policies, candidate = valid_fixture()
        held = dataclasses.replace(candidate, conditions=("virtual condition",))
        issues = validate_candidate(held, documents, policies)
        self.assertTrue(issues)
        with self.assertRaises(ValueError) as ctx:
            approve_candidate(
                CandidateReview(held, issues),
                documents,
                policies,
                candidates=(held,),
                reviewer="seo-yun",
                reviewed_at=REVIEWED_AT,
            )
        self.assertEqual(str(ctx.exception), "candidate_not_approvable")


class RefreshReviewTest(unittest.TestCase):
    def approve(
        self,
    ) -> tuple[CandidateReview, tuple[SourceDocument, ...], tuple[PolicyReference, ...]]:
        documents, policies, candidate = valid_fixture()
        review = CandidateReview(candidate, validate_candidate(candidate, documents, policies))
        approved = approve_candidate(
            review,
            documents,
            policies,
            candidates=(candidate,),
            reviewer="seo-yun",
            reviewed_at=REVIEWED_AT,
        )
        return approved, documents, policies

    def test_recollection_time_change_keeps_approval(self) -> None:
        approved, documents, policies = self.approve()
        recollected = tuple(
            dataclasses.replace(doc, collected_at=REVIEWED_AT + timedelta(hours=1))
            for doc in documents
        )
        refreshed = refresh_review(
            approved, recollected, policies, candidates=(approved.candidate,)
        )
        self.assertIsNotNone(refreshed.approval)
        self.assertEqual(refreshed.approval, approved.approval)
        self.assertEqual(refreshed.approval.reviewed_at, REVIEWED_AT)
        self.assertEqual(refreshed.issues, ())
        self.assertEqual(refreshed.status, "approved")
        self.assertEqual(refreshed.candidate, approved.candidate)

    def test_text_change_revokes_approval(self) -> None:
        approved, documents, policies = self.approve()
        changed = (dataclasses.replace(documents[0], text="changed virtual text"), documents[1])
        refreshed = refresh_review(approved, changed, policies, candidates=(approved.candidate,))
        self.assertIsNone(refreshed.approval)
        self.assertTrue(refreshed.issues)
        self.assertEqual(refreshed.status, "needs_review")
        self.assertEqual(refreshed.candidate, approved.candidate)

    def test_version_change_revokes_approval(self) -> None:
        approved, documents, policies = self.approve()
        changed = (dataclasses.replace(documents[0], document_version="v2026-02"), documents[1])
        refreshed = refresh_review(approved, changed, policies, candidates=(approved.candidate,))
        self.assertIsNone(refreshed.approval)
        self.assertTrue(refreshed.issues)

    def test_official_removed_revokes_approval(self) -> None:
        approved, documents, policies = self.approve()
        changed = (dataclasses.replace(documents[0], official=False), documents[1])
        refreshed = refresh_review(approved, changed, policies, candidates=(approved.candidate,))
        self.assertIsNone(refreshed.approval)
        self.assertTrue(refreshed.issues)

    def test_source_url_change_revokes_approval(self) -> None:
        approved, documents, policies = self.approve()
        changed = (
            dataclasses.replace(documents[0], source_url="https://example.invalid/docs/moved-01"),
            documents[1],
        )
        refreshed = refresh_review(approved, changed, policies, candidates=(approved.candidate,))
        self.assertIsNone(refreshed.approval)
        self.assertTrue(refreshed.issues)

    def test_added_document_revokes_approval(self) -> None:
        approved, documents, policies = self.approve()
        extra = make_document("doc-employment-extra", EMPLOYMENT_ID)
        refreshed = refresh_review(
            approved,
            (documents[0], documents[1], extra),
            policies,
            candidates=(approved.candidate,),
        )
        self.assertIsNone(refreshed.approval)
        self.assertTrue(refreshed.issues)

    def test_removed_document_revokes_approval(self) -> None:
        approved, documents, policies = self.approve()
        refreshed = refresh_review(
            approved, (documents[0],), policies, candidates=(approved.candidate,)
        )
        self.assertIsNone(refreshed.approval)
        self.assertTrue(refreshed.issues)

    def test_uncited_document_change_revokes_approval(self) -> None:
        approved, documents, policies = self.approve()
        extra = make_document("doc-employment-extra", EMPLOYMENT_ID)
        with_extra = (documents[0], documents[1], extra)
        base_review = CandidateReview(
            approved.candidate, validate_candidate(approved.candidate, with_extra, policies)
        )
        wide_approved = approve_candidate(
            base_review,
            with_extra,
            policies,
            candidates=(approved.candidate,),
            reviewer="seo-yun",
            reviewed_at=REVIEWED_AT,
        )
        changed_extra = dataclasses.replace(extra, text="changed virtual extra")
        refreshed = refresh_review(
            wide_approved,
            (documents[0], documents[1], changed_extra),
            policies,
            candidates=(approved.candidate,),
        )
        self.assertIsNone(refreshed.approval)
        self.assertTrue(refreshed.issues)

    def test_policy_scope_change_revokes_approval(self) -> None:
        approved, documents, policies = self.approve()
        narrowed = (
            make_policy(EMPLOYMENT_ID, ("employment",)),
            make_policy(HOUSING_ID, ("housing", "education")),
        )
        refreshed = refresh_review(approved, documents, narrowed, candidates=(approved.candidate,))
        self.assertIsNone(refreshed.approval)
        self.assertTrue(refreshed.issues)

    def test_candidate_tampering_revokes_approval(self) -> None:
        approved, documents, policies = self.approve()
        tampered_candidate = dataclasses.replace(approved.candidate, description="tampered virtual")
        tampered = dataclasses.replace(approved, candidate=tampered_candidate)
        refreshed = refresh_review(tampered, documents, policies, candidates=(tampered_candidate,))
        self.assertIsNone(refreshed.approval)
        self.assertTrue(refreshed.issues)
        self.assertEqual(refreshed.candidate, tampered_candidate)

    def test_refresh_preserves_candidate_on_hold(self) -> None:
        documents, policies, candidate = valid_fixture()
        held = dataclasses.replace(candidate, period="virtual period 2026")
        issues = validate_candidate(held, documents, policies)
        review = CandidateReview(held, issues)
        refreshed = refresh_review(review, documents, policies, candidates=(held,))
        self.assertIsNone(refreshed.approval)
        self.assertEqual(refreshed.candidate, held)
        self.assertTrue(refreshed.issues)
        self.assertEqual(refreshed.status, "needs_review")

    def test_new_conflicting_candidate_revokes_approval_on_refresh(self) -> None:
        approved, documents, policies = self.approve()
        rival = dataclasses.replace(valid_fixture()[2], id="candidate-virtual-rival")
        both = (approved.candidate, rival)
        refreshed = refresh_review(approved, documents, policies, candidates=both)
        self.assertIsNone(refreshed.approval)
        self.assertIn("conflicting_candidates", codes(refreshed.issues))
        self.assertEqual(refreshed.status, "needs_review")

    def test_missing_candidate_from_batch_holds_on_refresh(self) -> None:
        approved, documents, policies = self.approve()
        _, _, other_base = valid_fixture()
        other = dataclasses.replace(other_base, id="candidate-virtual-other")
        refreshed = refresh_review(approved, documents, policies, candidates=(other,))
        self.assertIsNone(refreshed.approval)
        self.assertTrue(refreshed.issues)

    def test_invalid_candidates_batch_holds_on_refresh(self) -> None:
        approved, documents, policies = self.approve()
        refreshed = refresh_review(approved, documents, policies, candidates=[approved.candidate])
        self.assertIsNone(refreshed.approval)
        self.assertTrue(refreshed.issues)

    def test_refresh_preserves_unresolved_review_issue_until_new_preparation(self) -> None:
        documents, policies, candidate = valid_fixture()
        pending = CandidateReview(candidate, (ReviewIssue("conflicting_sources"),))
        refreshed = refresh_review(pending, documents, policies, candidates=(candidate,))

        self.assertIn("conflicting_sources", codes(refreshed.issues))
        self.assertEqual(refreshed.status, "needs_review")
        with self.assertRaisesRegex(ValueError, "^candidate_not_approvable$"):
            approve_candidate(
                refreshed,
                documents,
                policies,
                candidates=(candidate,),
                reviewer="virtual-reviewer",
                reviewed_at=REVIEWED_AT,
            )

        prepared = prepare_rule_reviews(documents, policies, StaticExtractor((candidate,)))
        approved = approve_candidate(
            prepared.reviews[0],
            documents,
            policies,
            candidates=(candidate,),
            reviewer="virtual-reviewer",
            reviewed_at=REVIEWED_AT,
        )
        self.assertEqual(approved.status, "approved")

    def test_new_issue_suspends_existing_approval_and_survives_refresh(self) -> None:
        approved, documents, policies = self.approve()
        flagged = dataclasses.replace(approved, issues=(ReviewIssue("conflicting_sources"),))
        self.assertEqual(flagged.status, "needs_review")

        refreshed = refresh_review(flagged, documents, policies, candidates=(flagged.candidate,))
        self.assertIsNone(refreshed.approval)
        self.assertIn("conflicting_sources", codes(refreshed.issues))
        self.assertEqual(refreshed.status, "needs_review")
        self.assertEqual(approved.status, "approved")

    def test_document_change_reason_survives_repeated_refresh(self) -> None:
        documents, policies, original = valid_fixture()
        candidate = dataclasses.replace(original, citations=(original.citations[0],))
        prepared = prepare_rule_reviews(documents, policies, StaticExtractor((candidate,)))
        approved = approve_candidate(
            prepared.reviews[0],
            documents,
            policies,
            candidates=(candidate,),
            reviewer="virtual-reviewer",
            reviewed_at=REVIEWED_AT,
        )
        changed = (
            documents[0],
            dataclasses.replace(documents[1], text="changed virtual supplemental notice"),
        )
        revoked = refresh_review(approved, changed, policies, candidates=(candidate,))
        self.assertIsNone(revoked.approval)
        self.assertIn("documents_changed", codes(revoked.issues))

        refreshed = refresh_review(revoked, changed, policies, candidates=(candidate,))
        self.assertIn("documents_changed", codes(refreshed.issues))
        self.assertEqual(refreshed.status, "needs_review")
        with self.assertRaisesRegex(ValueError, "^candidate_not_approvable$"):
            approve_candidate(
                refreshed,
                changed,
                policies,
                candidates=(candidate,),
                reviewer="virtual-reviewer",
                reviewed_at=REVIEWED_AT,
            )


if __name__ == "__main__":
    unittest.main()
