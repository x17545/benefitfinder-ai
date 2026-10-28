
"""첫 번째 MVP 가상 정책 추천 서비스."""

from __future__ import annotations

from typing import Any

from benefitfinder.api.schemas.recommendation import RecommendationRequest
from benefitfinder.domain.portfolio import calculate_portfolios
from benefitfinder.domains.youth.policy_evaluator import evaluate_policies


MVP_AMOUNTS = {
    "1y": {
        "P1": 2_400_000,
        "P3": 3_000_000,
        "P6": 1_500_000,
    },
    "2y": {
        "P1": 2_400_000,
        "P3": 3_000_000,
        "P6": 3_000_000,
    },
    "all": {
        "P1": 2_400_000,
        "P3": 3_000_000,
        "P6": 3_000_000,
    },
}

MVP_CONFLICTS = [["P1", "P6"]]


def recommend_mvp(
    request: RecommendationRequest,
) -> dict[str, Any]:
    """프로필 자격 판단 결과를 이용해 가상 정책 조합을 계산한다."""

    if request.domain != "youth":
        raise ValueError("unsupported_domain")

    eligibility = evaluate_policies(request.profile)

    # needs_review는 제외하지 않고 주의 표시를 유지한다.
    candidates = [
        policy_id
        for policy_id, result in eligibility.items()
        if result["status"] != "not_met"
    ]

    period = (
        request.options.period
        if request.options and request.options.period
        else "1y"
    )

    amounts = MVP_AMOUNTS[period]

    portfolios = calculate_portfolios(
        eligible=candidates,
        amounts=amounts,
        conflicts=MVP_CONFLICTS,
    )

    return {
        "domain": request.domain,
        "period": period,
        "eligibility": eligibility,
        "portfolios": portfolios,
        "virtual_data": True,
    }


from benefitfinder.domain.present_value import calculate_period_value


MVP_PAYMENTS = {
    "P1": [
        (month, 200_000)
        for month in range(1, 13)
    ],
    "P3": [
        (month, 500_000)
        for month in range(1, 7)
    ],
    "P6": [
        (12, 1_500_000),
        (24, 1_500_000),
    ],
}


def recommend_mvp_all_periods(
    request: RecommendationRequest,
    annual_rate: float | None = 0.025,
) -> dict[str, Any]:
    """MVP의 3개 기간 × PV ON/OFF 결과를 계산한다."""

    if request.domain != "youth":
        raise ValueError("unsupported_domain")

    eligibility = evaluate_policies(request.profile)

    candidates = [
        policy_id
        for policy_id, result in eligibility.items()
        if result["status"] != "not_met"
    ]

    results = {}

    for period in ("1y", "2y", "all"):
        period_results = {}

        for pv_key in ("pv_off", "pv_on"):
            pv_enabled = pv_key == "pv_on"

            if pv_enabled and annual_rate is None:
                period_results[pv_key] = {
                    "calculation_status": "unavailable",
                    "unavailable_reason": "missing_discount_rate",
                    "portfolios": [],
                }
                continue

            amounts = {
                policy_id: calculate_period_value(
                    MVP_PAYMENTS[policy_id],
                    period,
                    annual_rate if pv_enabled else None,
                )
                for policy_id in candidates
            }

            portfolios = calculate_portfolios(
                eligible=candidates,
                amounts=amounts,
                conflicts=MVP_CONFLICTS,
            )

            period_results[pv_key] = {
                "calculation_status": "available",
                "unavailable_reason": None,
                "portfolios": portfolios,
            }

        results[period] = period_results

    return {
        "domain": request.domain,
        "eligibility": eligibility,
        "results": results,
        "virtual_data": True,
    }
