"""Applicant-specific source-cash assessment and payment resolution.

Published manual source terms remain conditional. These helpers evaluate only
the explicitly modelled source_cash rule and never infer missing facts.
"""

from __future__ import annotations

from datetime import date
import re
from typing import Any


_MAX_PAYMENT_OFFSET = 1200
_MONTH = re.compile(r"[0-9]{4}-(0[1-9]|1[0-2])")
_CHECK_NAMES = (
    "policy_id",
    "payment_month",
    "eligible_cost",
    "prior_support",
    "other_reimbursement",
    "other_exclusions_clear",
    "amount",
)


def _status(value: bool | None) -> str:
    return "needs_review" if value is None else "met" if value else "not_met"


def _matching_fact(profile: Any, policy_id: str):
    data = getattr(profile, "domain_data", None)
    facts = getattr(getattr(data, "cash_facts", None), "source_cash", None)
    if not isinstance(facts, (list, tuple)):
        return None
    matches = [item for item in facts if getattr(item, "policy_id", None) == policy_id]
    return matches[0] if len(matches) == 1 else None


def _valid_rule(rule: Any) -> bool:
    if not isinstance(rule, dict):
        return False
    amount = rule.get("amount_won")
    bps = rule.get("reimbursement_bps")
    return (
        isinstance(rule.get("policy_id"), str)
        and rule.get("mode") in {"fixed", "expense"}
        and type(amount) is int
        and 0 < amount <= 2**53 - 1
        and type(bps) is int
        and 1 <= bps <= 10000
        and (rule["mode"] != "fixed" or bps == 10000)
    )


def _month_offset(value: Any, today: date) -> tuple[int | None, bool | None]:
    if value is None:
        return None, None
    if not isinstance(value, str) or _MONTH.fullmatch(value) is None:
        return None, False
    year, month = map(int, value.split("-"))
    offset = (year - today.year) * 12 + month - today.month
    return offset, 0 <= offset <= _MAX_PAYMENT_OFFSET


def _zero_status(value: Any) -> bool | None:
    if value is None:
        return None
    return type(value) is int and value == 0


def _cost_status(fact: Any, mode: str) -> bool | None:
    cost = getattr(fact, "eligible_cost_won", None)
    if cost is None:
        return True if mode == "fixed" else None
    if type(cost) is not int or cost < 0:
        return False
    return True if mode == "fixed" else cost > 0


def _exclusions_status(value: Any) -> bool | None:
    if value is None:
        return None
    return value is True


def _evaluate(
    profile: Any,
    policy_id: str,
    rule: dict[str, Any],
    today: date,
) -> tuple[dict[str, str], int | None, int | None]:
    fact = _matching_fact(profile, policy_id)
    if fact is None or not _valid_rule(rule):
        return {name: "needs_review" for name in _CHECK_NAMES}, None, None
    if rule["policy_id"] != policy_id:
        return {name: "needs_review" for name in _CHECK_NAMES}, None, None

    mode = rule["mode"]
    offset, month_ok = _month_offset(getattr(fact, "payment_month", None), today)
    cost_ok = _cost_status(fact, mode)
    if mode == "expense" and cost_ok is True:
        actual = min(
            fact.eligible_cost_won * rule["reimbursement_bps"] // 10000,
            rule["amount_won"],
        )
        amount_ok: bool | None = True
    elif mode == "fixed":
        actual = rule["amount_won"]
        amount_ok = True
    else:
        actual = None
        amount_ok = cost_ok

    checks = {
        "policy_id": True,
        "payment_month": month_ok,
        "eligible_cost": cost_ok,
        "prior_support": _zero_status(getattr(fact, "prior_support_won", None)),
        "other_reimbursement": _zero_status(getattr(fact, "other_reimbursement_won", None)),
        "other_exclusions_clear": _exclusions_status(getattr(fact, "other_exclusions_clear", None)),
        "amount": amount_ok,
    }
    statuses = {name: _status(value) for name, value in checks.items()}
    if not all(value == "met" for value in statuses.values()):
        return statuses, None, None
    return statuses, offset, actual


def assess_source_cash(
    profile: Any,
    policy_id: str,
    rule: dict[str, Any],
    today: date,
) -> dict[str, str]:
    """Assess the exact policy fact and rule without using another policy's fact."""
    statuses, _, _ = _evaluate(profile, policy_id, rule, today)
    return statuses


def source_cash_payments(
    profile: Any,
    policy: Any,
    today: date,
) -> tuple[tuple[int, int], ...] | None:
    """Resolve one applicant-dependent source payment, or fall back to static payments."""
    conditions = getattr(policy, "conditions", None) or ()
    source_rules = [
        condition
        for condition in conditions
        if isinstance(condition, dict) and condition.get("kind") == "source_cash"
    ]
    if not source_rules:
        return getattr(policy, "payments", None)
    if len(source_rules) != 1:
        return None

    rule = source_rules[0].get("source_cash")
    if not isinstance(rule, dict):
        return None
    policy_id = getattr(policy, "policy_id", None)
    if not isinstance(policy_id, str):
        return None
    statuses, offset, amount = _evaluate(profile, policy_id, rule, today)
    if not all(value == "met" for value in statuses.values()):
        return None
    if offset is None or amount is None:
        return None
    return ((offset, amount),)


__all__ = ["assess_source_cash", "source_cash_payments"]
