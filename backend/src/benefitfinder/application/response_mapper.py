
"""MVP 추천 결과를 API 응답 형식으로 변환한다."""

from __future__ import annotations

from typing import Any


MVP_POLICY_SOURCES = {
    "P1": "https://policy.example/youth/sample-housing",
    "P3": "https://policy.example/youth/sample-p3",
    "P6": "https://policy.example/youth/sample-p6",
}


# 가상 MVP Fixture에 기록된 메타데이터
# 실제 정부 정책의 확인/갱신 시각이 아님
MVP_SOURCE_CHECKED_AT = "2026-10-26T09:00:00+09:00"
MVP_DATA_UPDATED_AT = "2026-10-26T03:12:00+09:00"


def map_portfolio_policy(
    policy_id: str,
    amount_won: int,
    original_amount_won: int,
    conditions: dict[str, str],
) -> dict[str, Any]:
    """가상 정책 하나를 응답 계약의 정책 객체로 변환한다."""

    if policy_id not in MVP_POLICY_SOURCES:
        raise ValueError(f"Unsupported policy: {policy_id}")


    eligibility = []

    for condition_id, status in conditions.items():
        # 현재는 정책별 근거 문서가 연결되지 않았다.
        # 근거 없는 확정 판정은 외부 응답에서 보류한다.
        eligibility.append(
            {
                "condition_id": condition_id,
                "status": "needs_review",
                "reason": (
                    "정책 근거 문서 확인이 필요합니다. "
                    f"내부 규칙 판정: {status}"
                ),
                "evidence": [],
            }
        )


    return {
        "policy_id": f"youth:example:{policy_id}",
        "amount_in_period_won": amount_won,
        "original_amount_won": original_amount_won,
        "amount_status": "unknown",
        "eligibility": eligibility,
        "ai_reason": None,
        "source": {
            "url": MVP_POLICY_SOURCES[policy_id],
            "checked_at": MVP_SOURCE_CHECKED_AT,
            "document_version": "fictional-v1",
        },
    }


def map_portfolio(
    portfolio: dict[str, Any],
    eligibility: dict[str, Any],
    amounts: dict[str, int],
    original_amounts: dict[str, int],
    pv_enabled: bool,
) -> dict[str, Any]:
    """계산된 추천 조합을 API 포트폴리오 형식으로 변환한다."""

    members = portfolio["members"]

    policies = [
        map_portfolio_policy(
            policy_id=policy_id,
            amount_won=amounts[policy_id],
            original_amount_won=original_amounts[policy_id],
            conditions=eligibility[policy_id]["conditions"],
        )
        for policy_id in members
    ]

    # P1과 P6은 가상 정책에서 중복 수혜 불가
    conflict_pair = {"P1", "P6"}

    given_up = []
    for policy_id in amounts:
        if policy_id in members:
            continue

        if (
            policy_id in conflict_pair
            and any(member in conflict_pair for member in members)
        ):
            given_up.append(
                {
                    "policy_id": f"youth:example:{policy_id}",
                    "amount_won": amounts[policy_id],
                }
            )

    # P1과 P3의 중복 수혜 여부는 아직 확인되지 않음
    unverified_pairs = []
    if {"P1", "P3"}.issubset(members):
        unverified_pairs.append(
            {
                "a": "youth:example:P1",
                "b": "youth:example:P3",
                "note": "[가상] 중복 수혜 여부 공식 자료 없음",
            }
        )

    return {
        "rank": portfolio["rank"],
        "total_won": portfolio["total_won"],
        "present_value_applied": pv_enabled,
        "policies": policies,
        "given_up": given_up,
        "unverified_pairs": unverified_pairs,
    }


from benefitfinder.domain.present_value import calculate_period_value
from benefitfinder.application.recommendation_service import (
    MVP_PAYMENTS,
)


def map_all_results(
    calculation: dict[str, Any],
    annual_rate: float | None,
) -> dict[str, Any]:
    """6가지 계산 결과의 포트폴리오를 상세 응답으로 변환한다."""

    mapped_results = {}

    original_amounts = {
        policy_id: sum(amount for _, amount in payments)
        for policy_id, payments in MVP_PAYMENTS.items()
    }

    for period, period_values in calculation["results"].items():
        mapped_period = {}

        for pv_key, result in period_values.items():
            pv_enabled = pv_key == "pv_on"

            if result["calculation_status"] == "unavailable":
                mapped_period[pv_key] = {
                    "calculation_status": "unavailable",
                    "unavailable_reason": result["unavailable_reason"],
                    "portfolios": [],
                }
                continue

            amounts = {
                policy_id: calculate_period_value(
                    payments,
                    period,
                    annual_rate if pv_enabled else None,
                )
                for policy_id, payments in MVP_PAYMENTS.items()
            }

            portfolios = [
                map_portfolio(
                    portfolio=portfolio,
                    eligibility=calculation["eligibility"],
                    amounts=amounts,
                    original_amounts=original_amounts,
                    pv_enabled=pv_enabled,
                )
                for portfolio in result["portfolios"]
            ]

            mapped_period[pv_key] = {
                "calculation_status": "available",
                "unavailable_reason": None,
                "portfolios": portfolios,
            }

        mapped_results[period] = mapped_period

    return mapped_results


from datetime import datetime, timezone
from uuid import uuid4


def build_recommendation_response(
    calculation: dict[str, Any],
    annual_rate: float | None,
    data_updated_at: str | None = None,
    discount_rate_verified: bool = False,
) -> dict[str, Any]:
    """MVP 계산 결과로 최상위 API 응답 객체를 생성한다."""

    results = map_all_results(
        calculation=calculation,
        annual_rate=annual_rate,
    )

    return {
        "request_id": str(uuid4()),
        "domain": calculation["domain"],
        "view": "portfolio",
        "results": results,
        "ai_generated": False,
        "data_updated_at": (
            data_updated_at
            if data_updated_at is not None
            else MVP_DATA_UPDATED_AT
        ),
        "discount_rate": annual_rate,

        "discount_rate_status": (
            "verified"
            if annual_rate is not None and discount_rate_verified
            else "unknown"
        ),

    }
