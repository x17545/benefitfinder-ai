"""Human decisions on individual F12 candidates, separate from rule approval and publication."""

from dataclasses import dataclass
from datetime import datetime

from benefitfinder.application.policy_facts import PolicyFactsDraft
from benefitfinder.application.policy_rules import RuleReviewSnapshot


@dataclass(frozen=True)
class F12ReviewDecision:
    item_id: str
    decision: str
    reviewer: str
    reason: str
    reviewed_at: datetime

    def __post_init__(self) -> None:
        if (
            self.decision not in {"approve", "reject"}
            or not 0 < len(self.reviewer.strip()) <= 128
            or not 0 < len(self.reason.strip()) <= 1000
            or self.reviewed_at.utcoffset() is None
        ):
            raise ValueError("invalid_f12_review_decision")


def f12_item_ids(
    snapshot: RuleReviewSnapshot, facts: PolicyFactsDraft
) -> dict[str, tuple[str, ...]]:
    """Number only actual candidates in their saved order within this pair."""
    counts = {
        "rule": len(snapshot.reviews),
        "eligibility": len(facts.eligibility),
        "amount": len(facts.amounts),
        "timing": len(facts.payment_timing),
    }
    return {
        kind: tuple(f"{kind}:{i}" for i in range(1, count + 1)) for kind, count in counts.items()
    }


def validate_f12_decisions(
    snapshot: RuleReviewSnapshot, facts: PolicyFactsDraft, decisions: tuple[F12ReviewDecision, ...]
) -> None:
    valid_ids = {item for group in f12_item_ids(snapshot, facts).values() for item in group}
    earliest = snapshot.created_at
    for entry in decisions:
        if entry.item_id not in valid_ids:
            raise ValueError("f12_review_item_not_found")
        if entry.reviewed_at < earliest:
            raise ValueError("invalid_f12_review_decision_time")
        earliest = entry.reviewed_at
