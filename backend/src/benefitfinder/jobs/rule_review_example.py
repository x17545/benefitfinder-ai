"""A synthetic local review batch, with no policy facts or external requests."""

from datetime import datetime, timezone

from benefitfinder.application.policy_rules import (
    PolicyReference,
    RuleCandidate,
    RuleCitation,
    RuleDirection,
    RuleKind,
    RuleReviewBatch,
    SourceDocument,
)


def example_rule_batch() -> RuleReviewBatch:
    quote = "[가상] 취업 지원 A와 주거 지원 B는 동시에 받을 수 없습니다."
    document = SourceDocument(
        id="virtual-notice-a",
        policy_id="youth:virtual:a",
        source_url="https://example.invalid/virtual-notice-a",
        document_version="virtual-v1",
        collected_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        text=quote,
        official=True,  # Synthetic provenance for this example only.
    )
    candidate = RuleCandidate(
        id="virtual-a-b",
        source_policy_id=document.policy_id,
        target_policy_ids=("youth:virtual:b",),
        kind=RuleKind.SIMULTANEOUS_EXCLUSION,
        description=quote,
        citations=(
            RuleCitation(
                document.id,
                document.document_version,
                document.content_sha256,
                quote,
                "가상 1절",
            ),
        ),
        direction=RuleDirection.BOTH,
    )
    return RuleReviewBatch(
        (document,),
        (
            PolicyReference(document.policy_id, "youth", ("employment",)),
            PolicyReference("youth:virtual:b", "youth", ("housing",)),
        ),
        (candidate,),
    )
