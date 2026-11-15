"""Expose reviewed maturity accruals separately from scheduled cash receipts."""

from benefitfinder.domain.portfolio import CalculationLimitExceeded
from benefitfinder.domain.present_value import calculate_period_value, maturity_breakdown


def amount_metadata(
    payments: dict[str, list[tuple[int, int]]],
    accruals: dict[str, list[tuple[int, int]]],
    period: str,
    members: list[str],
) -> dict[str, dict[str, int]]:
    if not any(key in accruals for key in members):
        return {}
    cash = sum(calculate_period_value(payments[key], period) for key in members)
    expected = sum(
        maturity_breakdown(payments[key], accruals[key], period)["expected_accrual_won"]
        for key in members if key in accruals
    )
    if cash + expected > 2**53 - 1:
        raise CalculationLimitExceeded("portfolio_amount_resource_limit")
    return {"amount_breakdown": {
        "cash_in_period_won": cash, "expected_accrual_won": expected,
    }}
