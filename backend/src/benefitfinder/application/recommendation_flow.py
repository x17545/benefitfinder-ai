"""Run the existing calculation, service lookup and explanation in real stages."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from benefitfinder.application.recommendation_explanations import RecommendationExplainer
from benefitfinder.application.recommendation_service import RecommendationRequestLike
from benefitfinder.application.response_mapper import build_recommendation_response
from benefitfinder.application.service_catalog import ServiceCatalog

Progress = Callable[[dict[str, str]], None]


async def run_recommendation(
    request: RecommendationRequestLike,
    *,
    calculate: Callable[..., dict[str, Any]],
    annual_rate: float | None,
    services: ServiceCatalog,
    explainer: RecommendationExplainer,
    progress: Progress,
) -> dict[str, Any]:
    step, label = "analyze_recommendations", "내 조건과 함께 받을 수 있는 조합 계산"
    progress({"step": step, "state": "running", "label": label})

    def calculation_response() -> dict[str, Any]:
        calculation = calculate(request, annual_rate=annual_rate)
        return build_recommendation_response(calculation=calculation, annual_rate=annual_rate)

    response = await asyncio.to_thread(calculation_response)
    progress({"step": step, "state": "done", "label": label})

    step, label = "search_services", "이용할 수 있는 서비스 확인"
    progress({"step": step, "state": "running", "label": label})
    response.update(await asyncio.to_thread(services.recommend, request.domain, request.profile))
    progress({"step": step, "state": "done", "label": label})

    step, label = "explain_with_sources", "원문 근거와 신청 안내 정리"
    progress({"step": step, "state": "running", "label": label})
    response = await explainer.enrich(response)
    progress({"step": step, "state": "done", "label": label})
    return response
