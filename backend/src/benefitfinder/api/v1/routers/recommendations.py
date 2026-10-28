
"""청년 정책 추천 API 라우터."""

from fastapi import APIRouter, HTTPException

from benefitfinder.api.schemas.recommendation import RecommendationRequest
from benefitfinder.application.recommendation_service import (
    recommend_mvp_all_periods,
)
from benefitfinder.application.response_mapper import (
    build_recommendation_response,
)

router = APIRouter(
    prefix="/api/v1",
    tags=["recommendations"],
)


@router.post("/recommendations")
def create_recommendations(request: RecommendationRequest):
    """가상 MVP 정책 추천 결과를 반환한다."""

    if request.domain != "youth":
        raise HTTPException(
            status_code=422,
            detail="unsupported_domain",
        )

    # 가상 MVP 계산용 할인율. 실제 금리 출처 검증값은 아님.
    annual_rate = 0.025

    calculation = recommend_mvp_all_periods(
        request,
        annual_rate=annual_rate,
    )

    response = build_recommendation_response(
        calculation=calculation,
        annual_rate=annual_rate,
    )

    return response
