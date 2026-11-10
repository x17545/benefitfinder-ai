"""첫 번째 MVP 가상 정책 자격 평가."""

from __future__ import annotations
from typing import Protocol

from benefitfinder.domains.youth.eligibility import (
    EligibilityStatus,
    check_age_with_military,
    check_job_seeker,
    check_home_ownership,
    check_monthly_rent,
    check_income_band,
    check_deposit_band,
    check_parent_income,
    combine_eligibility,
)

INCOME_LIMITS = {"P1": 30_000_000, "P6": 50_000_000}
AGE_LIMITS = {"P1": (19, 34, True), "P3": (19, 34, True), "P6": (19, 39, False)}


class YouthProfileLike(Protocol):
    """청년정책 평가에 필요한 사용자 조건의 최소 인터페이스."""

    age: int | None
    income_band: str | None
    employment_status: str | None
    domain_data: object | None


def evaluate_policies(profile: YouthProfileLike) -> dict:
    """P1, P3, P6의 조건별 상태와 종합 상태를 반환한다."""

    data = profile.domain_data

    military = data.military_months_range if data else None
    housing = data.housing if data else None
    contract = data.contract if data else None
    household = data.household if data else None

    military_min = military.min if military else None
    military_max = military.max if military else None

    ownership = housing.home_ownership if housing else None
    contract_type = contract.type if contract else None
    deposit_band = contract.deposit_band if contract else None
    parent_income = household.parent_income_band if household else None
    income_won = getattr(profile, "income_won", None)

    def income_status(policy: str) -> EligibilityStatus:
        limit = INCOME_LIMITS[policy]
        if income_won is not None:
            return "met" if income_won <= limit else "not_met"
        return check_income_band(profile.income_band, limit)

    common_age = {
        key: check_age_with_military(profile.age, lower, upper, military_min, military_max, extended)
        for key, (lower, upper, extended) in AGE_LIMITS.items()
    }

    conditions = {
        "P1": {
            "age": common_age["P1"],
            "home_ownership": check_home_ownership(ownership),
            "monthly_rent": check_monthly_rent(contract_type),
            "income": income_status("P1"),
            "deposit": check_deposit_band(deposit_band),
            "parent_income": check_parent_income(parent_income),
        },
        "P3": {
            "age": common_age["P3"],
            "employment": check_job_seeker(profile.employment_status),
        },
        "P6": {
            "age": common_age["P6"],
            "home_ownership": check_home_ownership(ownership),
            "income": income_status("P6"),
        },
    }

    return {
        policy_id: {
            "status": combine_eligibility(list(checks.values())),
            "conditions": checks,
        }
        for policy_id, checks in conditions.items()
    }
