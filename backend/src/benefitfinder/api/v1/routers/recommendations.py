"""청년 정책 추천 API 라우터."""

from fastapi import APIRouter, Depends, HTTPException, Request

from benefitfinder.api.schemas.recommendation import RecommendationRequest
from collections.abc import Callable
from typing import Any
from benefitfinder.application.recommendation_service import MVP_ANNUAL_RATE
from benefitfinder.application.response_mapper import (
    build_recommendation_response,
)
from benefitfinder.application.recommendation_explanations import RecommendationExplainer
from benefitfinder.application.service_catalog import ServiceCatalog


def get_recommendation_service(
    request: Request,
) -> Callable[..., dict[str, Any]]:
    """bootstrap.py에서 조립한 추천 서비스를 가져온다."""
    return request.app.state.recommendation_service


def get_recommendation_explainer(request: Request) -> RecommendationExplainer:
    """Return the F13 service assembled by bootstrap, without constructing an adapter."""
    return request.app.state.recommendation_explainer


def get_service_catalog(request: Request) -> ServiceCatalog:
    return request.app.state.service_catalog


router = APIRouter(
    prefix="/api/v1",
    tags=["recommendations"],
)


@router.post("/recommendations")
async def create_recommendations(
    request: RecommendationRequest,
    recommendation_service: Callable[..., dict[str, Any]] = Depends(get_recommendation_service),
    recommendation_explainer: RecommendationExplainer = Depends(get_recommendation_explainer),
    service_catalog: ServiceCatalog = Depends(get_service_catalog),
) -> dict[str, Any]:
    """가상 MVP 정책 추천 결과를 반환한다."""

    if request.domain != "youth":
        raise HTTPException(
            status_code=400,
            detail="unsupported_domain",
        )

    # 가상 MVP 계산용 할인율. 실제 금리 출처 검증값은 아님.
    annual_rate = MVP_ANNUAL_RATE

    calculation = recommendation_service(
        request,
        annual_rate=annual_rate,
    )

    response = build_recommendation_response(
        calculation=calculation,
        annual_rate=annual_rate,
    )
    response.update(service_catalog.recommend(request.domain, request.profile))

    return await recommendation_explainer.enrich(response)
