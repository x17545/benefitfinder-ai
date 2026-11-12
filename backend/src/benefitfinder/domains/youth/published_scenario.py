"""Hypothetical changes using the published rules, never the fixture thresholds."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any

from benefitfinder.domains.youth.published_eligibility import INCOME_BANDS


def change_published_profile(
    profile: Any,
    scenario: str,
    income_change: tuple[int, int] | None,
    *,
    rules: tuple[dict[str, Any], ...],
) -> tuple[Any, Any, tuple[int, int] | None, str]:
    before = SimpleNamespace(**deepcopy(vars(profile)))
    after = SimpleNamespace(**deepcopy(vars(profile)))
    before.income_won = after.income_won = None
    income = income_change
    note = ""
    band = INCOME_BANDS.get(profile.income_band)
    if income is not None and band is not None:
        lower, upper = band
        if income[0] < lower or (upper is not None and income[0] > upper):
            raise ValueError("inconsistent_income_assumption")
    if scenario in ("income_up", "employed"):
        if scenario == "employed":
            after.employment_status = "employed"
            if (
                income is None
                and profile.income_band == "none"
                and profile.employment_status != "employed"
            ):
                income = (0, 24_000_000)
                note = "취업 후 연 소득 2,400만 원을 가정했어요."
        elif income is None and band is not None:
            limits = sorted(
                {
                    rule["maximum"] - (0 if rule["inclusive"] else 1)
                    for rule in rules
                    if rule["kind"] == "income_max"
                }
            )
            threshold = next(
                (
                    limit
                    for limit in limits
                    if limit >= band[0] and (band[1] is None or limit <= band[1])
                ),
                None,
            )
            if threshold is not None:
                income = (threshold, threshold + 1)
                note = "게시된 소득 상한과 그 상한을 1원 넘는 경우를 가정했어요."
        if income is not None:
            before.income_won, after.income_won = income
        else:
            note = "비교할 소득 기준이나 대표값이 없어 소득 조건을 그대로 두었어요."
    elif scenario == "age_plus_one" and profile.age is not None:
        after.age += 1
    elif scenario == "independent":
        original_data = profile.domain_data
        original_housing = getattr(original_data, "housing", None)
        if getattr(original_housing, "living", None) != "alone":
            data = (
                SimpleNamespace(**deepcopy(vars(original_data)))
                if original_data
                else SimpleNamespace()
            )
            housing = (
                SimpleNamespace(**deepcopy(vars(original_housing)))
                if original_housing
                else SimpleNamespace()
            )
            housing.living = "alone"
            housing.household_head = "head"
            housing.home_ownership = getattr(original_housing, "home_ownership", None)
            data.housing = housing
            data.contract = SimpleNamespace(
                type="monthly_rent", deposit_band="under_50m", rent_band="between_40_60"
            )
            after.domain_data = data
            note = "독립 후 월세 계약(보증금 5천만 원 미만, 월세 40~60만 원)을 가정했어요."
    return before, after, income, note


def published_next_threshold(
    profile: Any,
    scenario: str,
    eligibility: dict[str, Any],
    *,
    policies: dict[str, tuple[str, tuple[dict[str, Any], ...]]],
) -> str | None:
    limits = []
    for key, (title, rules) in policies.items():
        if key not in eligibility or eligibility[key]["status"] == "not_met":
            continue
        for rule in rules:
            if rule["kind"] == "income_max" and scenario in ("income_up", "employed"):
                limit = rule["maximum"] - (0 if rule["inclusive"] else 1)
                if profile.income_won is not None and profile.income_won <= limit:
                    limits.append(
                        (limit, f"연 소득 {limit:,}원을 넘으면 {title}의 소득 기준을 벗어나요.")
                    )
            elif rule["kind"] == "age" and scenario == "age_plus_one":
                limit = rule["maximum"]
                if rule["military_extension_years"]:
                    military = getattr(profile.domain_data, "military_months_range", None)
                    cap = rule["military_extension_years"]
                    if military is None or military.min is None:
                        continue
                    lower = min(cap, military.min // 12)
                    upper = cap if military.max is None else min(cap, military.max // 12)
                    if lower != upper:
                        continue
                    limit += lower
                if profile.age is not None and profile.age <= limit:
                    limits.append(
                        (limit, f"만 {limit + 1}세가 되면 {title}의 나이 기준을 벗어나요.")
                    )
    return min(limits)[1] if limits else None
