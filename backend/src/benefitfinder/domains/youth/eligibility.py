
"""가상 청년정책 자격 판단 규칙."""

from __future__ import annotations

from typing import Literal

EligibilityStatus = Literal["met", "not_met", "needs_review"]


def check_age(
    age: int | None,
    minimum: int,
    maximum: int,
) -> EligibilityStatus:
    """군 복무 연장을 적용하지 않은 기본 연령 판정."""

    if age is None:
        return "needs_review"

    if age < minimum or age > maximum:
        return "not_met"

    return "met"


def check_age_with_military(
    age: int | None,
    minimum: int,
    maximum: int,
    military_min: int | None,
    military_max: int | None,
    extension_allowed: bool,
) -> EligibilityStatus:
    """군 복무 연장을 반영한 가상 정책 연령 판정."""

    basic = check_age(age, minimum, maximum)

    if basic == "met":
        return "met"

    if age is None:
        return "needs_review"

    # 최소 연령 미달은 군 복무 연장으로 해결되지 않는다.
    if age < minimum:
        return "not_met"

    if not extension_allowed:
        return "not_met"

    # 복무기간 정보가 없으면 연장 적용 여부를 확인해야 한다.
    if military_min is None or military_max is None:
        return "needs_review"

    if military_min > military_max:
        return "needs_review"

    # 이 MVP의 가상 정책에서만 적용하는 월 단위 연장 계산.
    minimum_extended_age = maximum + military_min // 12
    maximum_extended_age = maximum + military_max // 12

    if age <= minimum_extended_age:
        return "met"

    if age > maximum_extended_age:
        return "not_met"

    return "needs_review"


def check_job_seeker(
    employment_status: str | None,
) -> EligibilityStatus:
    """가상 P3 정책의 취업 상태 조건 판정."""

    if employment_status is None:
        return "needs_review"

    if employment_status == "job_seeker":
        return "met"

    if employment_status == "employed":
        return "not_met"

    # 기타 상태는 P3의 세부 자격 근거 확인 필요
    return "needs_review"


def check_home_ownership(
    home_ownership: str | None,
) -> EligibilityStatus:
    """가상 주거 정책의 무주택 조건 판정."""

    if home_ownership in (None, "unknown"):
        return "needs_review"

    if home_ownership == "homeless":
        return "met"

    if home_ownership == "owned":
        return "not_met"

    return "needs_review"


def check_monthly_rent(
    contract_type: str | None,
) -> EligibilityStatus:
    """가상 P1 정책의 월세 계약 조건 판정."""

    if contract_type is None:
        return "needs_review"

    if contract_type == "monthly_rent":
        return "met"

    if contract_type in ("jeonse", "owned", "none"):
        return "not_met"

    return "needs_review"



def check_income_band(
    income_band: str | None,
    maximum_won: int,
) -> EligibilityStatus:
    """연 소득 구간과 정책 소득 상한을 비교한다."""

    bands = {
        "none": (0, 0),
        "under_15m": (0, 15_000_000),
        "between_15m_30m": (15_000_000, 30_000_000),
        "between_30m_50m": (30_000_000, 50_000_000),
        "over_50m": (50_000_000, None),
    }

    if income_band in (None, "unknown"):
        return "needs_review"

    if income_band not in bands:
        return "needs_review"

    minimum, maximum = bands[income_band]

    # 상한이 없는 구간: 5,000만 원 초과
    if income_band == "over_50m":
        if maximum_won <= 50_000_000:
            return "not_met"
        return "needs_review"

    # 구간 전체가 정책 소득 기준 이하
    if maximum is not None and maximum <= maximum_won:
        return "met"

    # 구간 전체가 정책 소득 기준 초과
    if minimum > maximum_won:
        return "not_met"

    # 경계값을 가로지르는 구간
    return "needs_review"


def check_deposit_band(
    deposit_band: str | None,
) -> EligibilityStatus:
    """가상 P1 정책의 보증금 5천만 원 이하 조건 판정."""

    if deposit_band is None:
        return "needs_review"

    if deposit_band == "under_50m":
        return "met"

    if deposit_band in (
        "between_50m_100m",
        "between_100m_300m",
        "over_300m",
    ):
        return "needs_review"

    return "needs_review"


def check_parent_income(
    parent_income_band: str | None,
) -> EligibilityStatus:
    """가상 P1 정책의 부모 소득 확인 상태."""

    # 구체적인 정책 기준이 아직 없으므로
    # 입력 여부에 관계없이 자격을 확정하지 않는다.
    return "needs_review"


def combine_eligibility(
    statuses: list[EligibilityStatus],
) -> EligibilityStatus:
    """개별 조건의 자격 상태를 하나로 합친다."""

    if not statuses:
        return "needs_review"

    if "not_met" in statuses:
        return "not_met"

    if "needs_review" in statuses:
        return "needs_review"

    return "met"
