"""Search, diagnose and calculate from one published catalogue snapshot."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from itertools import combinations
from time import monotonic
from typing import Any
from uuid import uuid4

from benefitfinder.application.published_policies import PublishedPolicy, PublishedPolicyRepository
from benefitfinder.application.recruitment import application_on
from benefitfinder.domain.portfolio import CalculationLimitExceeded, calculate_portfolios
from benefitfinder.domain.present_value import calculate_period_value


def korea_today() -> date:
    return datetime.now(timezone(timedelta(hours=9))).date()


def policy_detail(policy: PublishedPolicy, today: date) -> dict[str, Any]:
    detail = deepcopy(policy.detail)
    detail["application"] = application_on(detail["application"], today)
    return detail


class PublishedRecommendations:
    def __init__(
        self,
        repository: PublishedPolicyRepository,
        evaluate: Callable[[Any, dict[str, Any]], str],
        *,
        today: Callable[[], date] = korea_today,
    ) -> None:
        self.repository = repository
        self.evaluate = evaluate
        self.today = today

    def detail(self, policy_id: str) -> dict[str, Any] | None:
        policy = self.repository.get_published(policy_id)
        return policy_detail(policy, self.today()) if policy is not None else None

    def __call__(self, request: Any, annual_rate: float | None = None) -> dict[str, Any]:
        deadline = monotonic() + 45
        return self.calculate(
            request, self.repository.list_published(), annual_rate, deadline=deadline
        )

    def calculate(
        self,
        request: Any,
        policies: tuple[PublishedPolicy, ...],
        annual_rate: float | None = None,
        *,
        deadline: float | None = None,
    ) -> dict[str, Any]:
        if request.domain != "youth":
            raise ValueError("unsupported_domain")
        deadline = deadline if deadline is not None else monotonic() + 45
        updated_at = max((p.published_at for p in policies), default="1970-01-01T00:00:00+00:00")
        interests = getattr(request.profile.domain_data, "interests", None)
        policies = tuple(
            p
            for p in policies
            if not interests or set(interests).intersection(p.detail["categories"])
        )
        today = self.today()
        details = {p.policy_id: policy_detail(p, today) for p in policies}
        by_id = {p.policy_id: p for p in policies}
        eligibility: dict[str, Any] = {}
        diagnosis = {}
        for policy in policies:
            results = {
                rule["condition_id"]: self.evaluate(request.profile, rule)
                for rule in policy.conditions
            }
            status = (
                "not_met"
                if "not_met" in results.values()
                else "needs_review"
                if not results or "needs_review" in results.values()
                else "met"
            )
            eligibility[policy.policy_id] = {"status": status, "conditions": results}
            diagnosis[policy.policy_id] = [
                {
                    "condition_id": condition["id"],
                    "status": results[condition["id"]],
                    "reason": {
                        "met": "입력 조건이 기준에 맞아요.",
                        "not_met": "입력 조건이 기준에 맞지 않아요.",
                        "needs_review": "입력 정보나 기준 적용을 확인해야 해요.",
                    }[results[condition["id"]]],
                    "evidence": deepcopy(condition["evidence"]),
                }
                for condition in policy.detail["eligibility_conditions"]
            ]
        candidates = [
            p.policy_id
            for p in policies
            if eligibility[p.policy_id]["status"] != "not_met"
            and details[p.policy_id]["application"]["status"] == "open"
            and p.detail["reviewed"]
            and p.payments is not None
        ]
        conflicts = [
            [p.policy_id, conflict["policy_id"]]
            for p in policies
            if p.policy_id in candidates
            for conflict in p.detail["conflicts"]
            if conflict["policy_id"] in candidates
        ]
        if len(candidates) > 128:
            raise CalculationLimitExceeded("policy_candidate_resource_limit")
        conflict_sets = {frozenset(pair) for pair in conflicts}
        unverified = [
            list(pair)
            for pair in combinations(candidates, 2)
            if frozenset(pair) not in conflict_sets
        ]
        payments = {key: list(by_id[key].payments or ()) for key in candidates}
        results = {}
        for period in ("1y", "2y", "all"):
            results[period] = {}
            for pv in ("pv_off", "pv_on"):
                if pv == "pv_on" and annual_rate is None:
                    results[period][pv] = {
                        "calculation_status": "unavailable",
                        "unavailable_reason": "missing_discount_rate",
                        "portfolios": [],
                    }
                    continue
                amounts = {
                    key: calculate_period_value(
                        value, period, annual_rate if pv == "pv_on" else None
                    )
                    for key, value in payments.items()
                }
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise CalculationLimitExceeded("recommendation_time_limit")
                portfolios = calculate_portfolios(
                    candidates,
                    amounts,
                    conflicts,
                    unverified_pairs=unverified,
                    max_search_nodes=250_000,
                    timeout_seconds=remaining,
                )
                if any(p["total_won"] > 2**53 - 1 for p in portfolios):
                    raise CalculationLimitExceeded("portfolio_amount_resource_limit")
                results[period][pv] = {
                    "calculation_status": "available",
                    "unavailable_reason": None,
                    "portfolios": portfolios,
                    "amounts": amounts,
                }
        return {
            "domain": "youth",
            "eligibility": eligibility,
            "results": results,
            "virtual_data": any(p.detail["virtual_data"] for p in policies),
            "catalog": {
                "details": details,
                "diagnosis": diagnosis,
                "conflicts": conflicts,
                "unverified": unverified,
                "payments": payments,
                "updated_at": updated_at,
            },
        }


def map_published_response(
    calculation: dict[str, Any], annual_rate: float | None
) -> dict[str, Any]:
    catalog = calculation["catalog"]
    details, diagnosis = catalog["details"], catalog["diagnosis"]
    conflicts = {frozenset(pair) for pair in catalog["conflicts"]}
    mapped = {}
    for period, period_results in calculation["results"].items():
        mapped[period] = {}
        for pv, view in period_results.items():
            result = {
                "calculation_status": view["calculation_status"],
                "unavailable_reason": view["unavailable_reason"],
                "portfolios": [],
            }
            mapped[period][pv] = result
            result["closed_now"] = []
            result["not_eligible"] = []
            result["not_yet_reviewed"] = []
            for key, detail in details.items():
                status = calculation["eligibility"][key]["status"]
                application = detail["application"]
                if application["status"] == "closed":
                    result["closed_now"].append(
                        {"policy_id": key, "last_period": application["ends_on"]}
                    )
                elif status == "not_met":
                    result["not_eligible"].append(
                        {
                            "policy_id": key,
                            "failed": [
                                item["condition_id"]
                                for item in diagnosis[key]
                                if item["status"] == "not_met"
                            ],
                        }
                    )
                elif key not in catalog["payments"]:
                    result["not_yet_reviewed"].append(
                        {"policy_id": key, "eligibility_summary": status}
                    )
            if view["calculation_status"] != "available":
                continue
            amounts = view["amounts"]
            if not any(frozenset(pair) not in conflicts for pair in combinations(amounts, 2)):
                result["fallback_list"] = [
                    {
                        "policy_id": key,
                        "amount_in_period_won": amounts[key],
                        "reason": "함께 받을 두 정책 이상인 조합을 만들 수 없어 개별 정책을 표시해요.",
                    }
                    for key in sorted(amounts, key=lambda key: (-amounts[key], key))[:5]
                ]
                continue
            for portfolio in view["portfolios"]:
                members = portfolio["members"]
                result["portfolios"].append(
                    {
                        "rank": portfolio["rank"],
                        "total_won": portfolio["total_won"],
                        "present_value_applied": pv == "pv_on",
                        "policies": [
                            {
                                "policy_id": key,
                                "amount_in_period_won": amounts[key],
                                "original_amount_won": sum(a for _, a in catalog["payments"][key]),
                                "amount_status": "verified",
                                "eligibility": deepcopy(diagnosis[key]),
                                "source": deepcopy(details[key]["source"]),
                                "ai_reason": None,
                            }
                            for key in members
                        ],
                        "given_up": [
                            {"policy_id": key, "amount_won": amounts[key]}
                            for key in amounts
                            if key not in members
                            and any(frozenset((key, m)) in conflicts for m in members)
                        ],
                        "unverified_pairs": [
                            {"a": a, "b": b, "note": "동시 수급을 확인하는 공식 근거가 없어요."}
                            for a, b in catalog["unverified"]
                            if a in members and b in members
                        ],
                    }
                )
            first = view["portfolios"][0]
            selected = set(first["members"])
            result["excluded"] = []
            for key in amounts:
                if key in selected:
                    continue
                evidence = [
                    item
                    for policy_id, detail in details.items()
                    for conflict in detail["conflicts"]
                    if (policy_id == key and conflict["policy_id"] in selected)
                    or (policy_id in selected and conflict["policy_id"] == key)
                    for item in conflict["evidence"]
                ]
                if evidence:
                    result["excluded"].append(
                        {
                            "policy_id": key,
                            "reason": "추천 조합 안의 정책과 함께 받을 수 없어요.",
                            "evidence": deepcopy(evidence),
                        }
                    )
            if len(view["portfolios"]) > 1:
                second = view["portfolios"][1]
                different = selected.symmetric_difference(second["members"])
                deciding = sorted(sorted(pair) for pair in conflicts if pair <= different)
                result["why"] = {
                    "gap_won": first["total_won"] - second["total_won"],
                    "second_total_won": second["total_won"],
                    "deciding_conflict": deciding[0] if deciding else [],
                }
    initial = mapped["1y"]["pv_off"]
    return {
        "request_id": str(uuid4()),
        "domain": "youth",
        "virtual_data": calculation["virtual_data"],
        "view": "portfolio"
        if initial["portfolios"]
        else "fallback_list"
        if initial.get("fallback_list")
        else "empty",
        "results": mapped,
        "ai_generated": False,
        "data_updated_at": catalog["updated_at"],
        "discount_rate": annual_rate,
        "discount_rate_status": "verified" if annual_rate is not None else "unknown",
    }
