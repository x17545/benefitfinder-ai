"""Assumptions for the youth MVP's four hypothetical changes."""

from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

from benefitfinder.domains.youth.policy_evaluator import AGE_LIMITS, INCOME_LIMITS

INCOME_BANDS: dict[str, tuple[int, int | None]] = {
    "none": (0, 0),
    "under_15m": (1, 14_999_999),
    "between_15m_30m": (15_000_000, 30_000_000),
    "between_30m_50m": (30_000_001, 50_000_000),
    "over_50m": (50_000_001, None),
}
INCOME_MIDPOINTS = {
    "none": 0,
    "under_15m": 7_500_000,
    "between_15m_30m": 22_500_000,
    "between_30m_50m": 40_000_000,
}


def _income_increase(band: str | None) -> tuple[int, int] | None:
    if band in ("none", "under_15m"):
        return (0, 7_500_000) if band == "none" else (7_500_000, 22_500_000)
    # Test the cliff just above the upper end of a known band, not its midpoint.
    if band in INCOME_BANDS:
        upper = INCOME_BANDS[band][1]
        if upper in INCOME_LIMITS.values():
            return upper - 1_000_000, upper + 1_000_000
    return None


def change_profile(
    profile: Any, scenario: str, income_change: tuple[int, int] | None,
) -> tuple[Any, Any, tuple[int, int] | None, str]:
    """Project the hypothetical profiles without changing the recommendation input."""
    data = profile.domain_data
    housing = data.housing if data else None
    age = profile.age
    employment = profile.employment_status
    note = ""
    income = None

    if scenario in ("employed", "income_up"):
        income = income_change
        if income is not None and profile.income_band in INCOME_BANDS:
            lower, upper = INCOME_BANDS[profile.income_band]
            if income[0] < lower or (upper is not None and income[0] > upper):
                raise ValueError("inconsistent_income_assumption")
        if scenario == "employed":
            employment = "employed"
            if income is None and profile.income_band == "none" and profile.employment_status != employment:
                income = (0, 24_000_000)
        elif income is None:
            income = _income_increase(profile.income_band)
        if income is None:
            note = "소득 변화의 대표값이 없어 입력한 소득 조건을 그대로 두었어요."
    elif scenario == "independent" and (housing is None or housing.living != "alone"):
        data = SimpleNamespace(
            military_months_range=data.military_months_range if data else None,
            housing=SimpleNamespace(
                living="alone",
                household_head="head",
                home_ownership=housing.home_ownership if housing else None,
            ),
            contract=SimpleNamespace(
                type="monthly_rent", deposit_band="under_50m", rent_band="between_40_60",
            ),
            household=data.household if data else None,
        )
        note = "독립 후 월세 계약(보증금 5천만 원 미만, 월세 40~60만 원)을 가정했어요."
    elif scenario == "age_plus_one" and age is not None:
        age += 1

    after = SimpleNamespace(
        age=age,
        employment_status=employment,
        income_band=profile.income_band,
        income_won=income[1] if income is not None else None,
        domain_data=data,
    )
    annual_before = profile if income is None else SimpleNamespace(
        age=profile.age,
        employment_status=profile.employment_status,
        income_band=profile.income_band,
        income_won=income[0],
        domain_data=profile.domain_data,
    )
    return annual_before, after, income, note


def next_threshold(
    profile: Any, scenario: str, eligibility: dict[str, Any], *, policy_names: Mapping[str, str],
) -> str | None:
    """Report the nearest supported boundary among remaining candidates."""
    limits: list[tuple[int, str]] = []
    if scenario in ("employed", "income_up") and profile.income_won is not None:
        limits = sorted(
            (limit, policy) for policy, limit in INCOME_LIMITS.items()
            if profile.income_won <= limit and eligibility[policy]["status"] != "not_met"
        )
        if limits:
            limit, policy = limits[0]
            return f"연 소득 {limit // 10_000:,}만 원을 넘으면 {policy_names[policy]}의 소득 기준을 벗어나요."
    if scenario == "age_plus_one" and profile.age is not None:
        military = profile.domain_data.military_months_range if profile.domain_data else None
        for policy, (_, upper, extended) in AGE_LIMITS.items():
            if extended:
                if (
                    military is None or military.min is None or military.max is None
                    or military.min // 12 != military.max // 12
                ):
                    continue
                upper += military.min // 12
            if profile.age <= upper and eligibility[policy]["status"] != "not_met":
                limits.append((upper, policy))
        if limits:
            upper, policy = min(limits)
            return f"만 {upper + 1}세가 되면 {policy_names[policy]}의 나이 기준을 벗어나요."
    return None
