"""Local F12 fact candidates, separate from the published policy contract."""

from dataclasses import dataclass

from benefitfinder.application.policy_rules import RuleCitation, SourceDocument


@dataclass(frozen=True)
class PolicyAmountCandidate:
    evidence: RuleCitation
    amounts_won: tuple[int, ...]


@dataclass(frozen=True)
class PolicyFactsDraft:
    document: SourceDocument
    eligibility: tuple[RuleCitation, ...]
    amounts: tuple[PolicyAmountCandidate, ...]
    payment_timing: tuple[RuleCitation, ...]
    scheduled_monthly_day: int | None = None
