"""청년 정책 추천 API 라우터."""

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from benefitfinder.api.schemas.recommendation import RecommendationRequest
from collections.abc import Callable
from typing import Any
from benefitfinder.application.policy_explanations import ExplanationRequest
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


def get_discount_rate(request: Request) -> float | None:
    return request.app.state.discount_rate


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
    annual_rate: float | None = Depends(get_discount_rate),
) -> dict[str, Any]:
    """조립된 게시 정책 또는 명시적인 개발 예제로 추천한다."""

    if request.domain != "youth":
        raise HTTPException(
            status_code=400,
            detail="unsupported_domain",
        )

    calculation = await run_in_threadpool(
        recommendation_service,
        request,
        annual_rate=annual_rate,
    )

    response = build_recommendation_response(
        calculation=calculation,
        annual_rate=annual_rate,
    )
    response.update(service_catalog.recommend(request.domain, request.profile))

    if "catalog" in calculation:
        policies = tuple(
            ExplanationRequest(
                policy_id=key, policy_name=detail["title"], category=detail["categories"][0],
                document_version=detail["source"]["document_version"], amount_won=None,
            ) for key, detail in calculation["catalog"]["details"].items()
        )
        return await recommendation_explainer.enrich(response, policies=policies)
    return await recommendation_explainer.enrich(response)
