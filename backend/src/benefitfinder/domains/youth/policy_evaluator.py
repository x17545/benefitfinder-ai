
"""첫 번째 MVP 가상 정책 자격 평가."""

from __future__ import annotations

from benefitfinder.api.schemas.recommendation import UserProfile
from benefitfinder.domains.youth.eligibility import (
    check_age_with_military,
    check_job_seeker,
    check_home_ownership,
    check_monthly_rent,
    check_income_band,
    check_deposit_band,
    check_parent_income,
    combine_eligibility,
)


def evaluate_policies(profile: UserProfile) -> dict:
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

    common_age = {
        "P1": check_age_with_military(
            profile.age, 19, 34, military_min, military_max, True
        ),
        "P3": check_age_with_military(
            profile.age, 19, 34, military_min, military_max, True
        ),
        "P6": check_age_with_military(
            profile.age, 19, 39, military_min, military_max, False
        ),
    }

    conditions = {
        "P1": {
            "age": common_age["P1"],
            "home_ownership": check_home_ownership(ownership),
            "monthly_rent": check_monthly_rent(contract_type),
            "income": check_income_band(profile.income_band, 30_000_000),
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
            "income": check_income_band(profile.income_band, 50_000_000),
        },
    }

    return {
        policy_id: {
            "status": combine_eligibility(list(checks.values())),
            "conditions": checks,
        }
        for policy_id, checks in conditions.items()
    }
