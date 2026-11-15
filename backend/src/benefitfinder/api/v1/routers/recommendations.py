"""청년 정책 추천 API 라우터."""

from collections.abc import Callable
from typing import Any
from time import monotonic

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from benefitfinder.api.dependencies.analysis_usage import reserve_analysis
from benefitfinder.api.analysis_response import analysis_response, wants_event_stream
from benefitfinder.api.schemas.recommendation import RecommendationRequest
from benefitfinder.application.recommendation_explanations import RecommendationExplainer
from benefitfinder.application.recommendation_flow import run_recommendation
from benefitfinder.application.service_catalog import ServiceRecommendations


def get_recommendation_service(
    request: Request,
) -> Callable[..., dict[str, Any]]:
    """bootstrap.py에서 조립한 추천 서비스를 가져온다."""
    return request.app.state.recommendation_service


def get_recommendation_explainer(request: Request) -> RecommendationExplainer:
    """Return the F13 service assembled by bootstrap, without constructing an adapter."""
    return request.app.state.recommendation_explainer


def get_service_catalog(request: Request) -> ServiceRecommendations:
    return request.app.state.service_catalog


def get_discount_rate(request: Request) -> float | None:
    request.state.analysis_started_at = getattr(request.state, "analysis_started_at", monotonic())
    return request.app.state.read_discount_rate()


router = APIRouter(
    prefix="/api/v1",
    tags=["recommendations"],
)


@router.post(
    "/recommendations",
    response_model=None,
    responses={
        200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}},
    },
)
async def create_recommendations(
    request: RecommendationRequest,
    http_request: Request,
    recommendation_service: Callable[..., dict[str, Any]] = Depends(get_recommendation_service),
    recommendation_explainer: RecommendationExplainer = Depends(get_recommendation_explainer),
    service_catalog: ServiceRecommendations = Depends(get_service_catalog),
    annual_rate: float | None = Depends(get_discount_rate),
) -> Response:
    """조립된 게시 정책 또는 명시적인 개발 예제로 추천한다."""

    if request.domain != "youth":
        raise HTTPException(
            status_code=400,
            detail="unsupported_domain",
        )

    await reserve_analysis(http_request)

    async def analyze(progress):
        return await run_recommendation(
            request,
            calculate=recommendation_service,
            annual_rate=annual_rate,
            services=service_catalog,
            explainer=recommendation_explainer,
            progress=progress,
        )

    return await analysis_response(
        http_request,
        analyze,
        timeout_seconds=http_request.app.state.analysis_timeout_seconds,
        stream=wants_event_stream(http_request.headers.get("accept", "")),
    )
