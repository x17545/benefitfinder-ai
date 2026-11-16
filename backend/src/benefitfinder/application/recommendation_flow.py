"""Run the existing calculation, service lookup and explanation in real stages."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from typing import Any

from benefitfinder.application.recommendation_explanations import RecommendationExplainer
from benefitfinder.application.policy_explanations import ExplanationRequest
from benefitfinder.application.recommendation_service import RecommendationRequestLike
from benefitfinder.application.response_mapper import build_recommendation_response, map_policy_summaries
from benefitfinder.application.service_catalog import ServiceRecommendations

Progress = Callable[[dict[str, str]], None]


async def run_recommendation(
    request: RecommendationRequestLike,
    *,
    calculate: Callable[..., dict[str, Any]],
    annual_rate: float | None,
    services: ServiceRecommendations,
    explainer: RecommendationExplainer,
    progress: Progress,
    example_details: Mapping[str, dict[str, Any]],
) -> dict[str, Any]:
    step, label = "analyze_recommendations", "내 조건과 함께 받을 수 있는 조합 계산"
    progress({"step": step, "state": "running", "label": label})

    def calculation_response() -> tuple[dict[str, Any], tuple[ExplanationRequest, ...] | None]:
        calculation = calculate(request, annual_rate=annual_rate)
        response = build_recommendation_response(calculation=calculation, annual_rate=annual_rate)
        catalog = calculation.get("catalog")
        details = example_details if catalog is None else catalog["details"]
        response["policy_summaries"] = map_policy_summaries(response["results"], details)
        policies = None
        if catalog is not None:
            policies = tuple(
                ExplanationRequest(
                    policy_id=key,
                    policy_name=detail["title"],
                    category=detail["categories"][0],
                    document_version=detail["source"]["document_version"],
                    amount_won=None,
                )
                for key, detail in details.items()
            )
        return response, policies

    response, policies = await asyncio.to_thread(calculation_response)
    progress({"step": step, "state": "done", "label": label})

    step, label = "search_services", "이용할 수 있는 서비스 확인"
    progress({"step": step, "state": "running", "label": label})
    service_result = dict(await asyncio.to_thread(services.recommend, request.domain, request.profile))
    service_virtual_data = service_result.pop("virtual_data", False)
    response["virtual_data"] = bool(response.get("virtual_data") or service_virtual_data)
    response.update(service_result)
    progress({"step": step, "state": "done", "label": label})

    step, label = "explain_with_sources", "원문 근거와 신청 안내 정리"
    progress({"step": step, "state": "running", "label": label})
    response = (
        await explainer.enrich(response, policies=policies)
        if policies is not None
        else await explainer.enrich(response)
    )
    progress({"step": step, "state": "done", "label": label})
    return response
