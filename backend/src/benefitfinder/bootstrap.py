"""Concrete application assembly.

Only this module and the ASGI entrypoint (``main.py``) may wire concrete
pieces together. Routers, schemas, and dependencies never import adapters
directly; they receive already-assembled objects here.
"""

from __future__ import annotations

import uuid
import math
from functools import partial
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from benefitfinder.api.schemas import ErrorCode, ErrorResponse
from benefitfinder.api.v1.routers.benefits import router as benefits_router
from benefitfinder.api.v1.routers.health import router as health_router
from benefitfinder.api.v1.routers.options import router as options_router
from benefitfinder.api.v1.routers.recommendations import router as recommendations_router
from benefitfinder.api.v1.routers.simulations import router as simulations_router
from benefitfinder.ai.policy_explanation import GroundedPolicyExplainer
from benefitfinder.application.policy_explanations import (
    ExplanationGenerator,
    PublishedClauseRepository,
)
from benefitfinder.application.recommendation_explanations import RecommendationExplainer
from benefitfinder.application.recommendation_service import (
    recommend_mvp_all_periods,
)
from benefitfinder.application.region_options import RegionCatalog
from benefitfinder.application.service_catalog import ServiceCatalog
from benefitfinder.application.simulation_service import simulate_mvp
from benefitfinder.domains.youth.policy_evaluator import evaluate_policies
from benefitfinder.domains.youth.scenario import change_profile, next_threshold
from benefitfinder.infrastructure.database.repositories.published_clauses import (
    PostgresPublishedClauses,
)
from benefitfinder.infrastructure.example_explanation_data import recommendation_explanation_data
from benefitfinder.infrastructure.example_benefits import example_benefits
from benefitfinder.infrastructure.example_services import example_services
from benefitfinder.infrastructure.ollama_explanations import OllamaExplanationCompletion
from benefitfinder.infrastructure.published_clauses import InMemoryPublishedClauses
from benefitfinder.infrastructure.region_codes import OfficialRegionCatalog

NOT_FOUND_MESSAGE = "요청한 리소스를 찾을 수 없습니다."
INVALID_REQUEST_MESSAGE = "요청 값이 올바르지 않습니다."
INTERNAL_ERROR_MESSAGE = "내부 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
HTTP_ERROR_RESPONSES: dict[int, tuple[ErrorCode, str]] = {
    404: ("not_found", NOT_FOUND_MESSAGE),
    405: ("method_not_allowed", "지원하지 않는 HTTP 메서드입니다."),
    422: ("invalid_request", INVALID_REQUEST_MESSAGE),
    500: ("internal_error", INTERNAL_ERROR_MESSAGE),
    503: ("dependency_unavailable", "서비스를 일시적으로 사용할 수 없습니다."),
}


def error_body(code: ErrorCode, message: str) -> dict[str, Any]:
    """Build a common-format error payload with a fresh UUID request id."""
    return ErrorResponse(request_id=str(uuid.uuid4()), code=code, message=message).model_dump()


def create_app(
    *,
    region_catalog: RegionCatalog | None = None,
    explanation_model: str | None = None,
    explanation_repository: PublishedClauseRepository | None = None,
    explanation_generator: ExplanationGenerator | None = None,
    explanation_timeout_seconds: float = 5,
    explanation_database_url: str | None = None,
    service_catalog: ServiceCatalog | None = None,
    analysis_timeout_seconds: float = 60,
) -> FastAPI:
    """Assemble and return a new FastAPI application.

    Each call returns an independent instance; routes added to one instance
    never affect another. The app starts without DB, LLM, or secrets.
    """
    if explanation_model is not None and explanation_generator is not None:
        raise ValueError("ambiguous_explanation_generator")
    if (
        isinstance(analysis_timeout_seconds, bool)
        or not isinstance(analysis_timeout_seconds, (float, int))
        or not math.isfinite(analysis_timeout_seconds)
        or not 0 < analysis_timeout_seconds <= 60
    ):
        raise ValueError("invalid_analysis_timeout")
    if explanation_repository is not None and explanation_database_url is not None:
        raise ValueError("ambiguous_explanation_repository")
    if explanation_database_url is not None:
        explanation_repository = PostgresPublishedClauses(explanation_database_url)
    if explanation_model is not None:
        explanation_generator = GroundedPolicyExplainer(
            OllamaExplanationCompletion(explanation_model)
        )
    policies, clauses = recommendation_explanation_data()
    explainer = RecommendationExplainer(
        explanation_repository
        if explanation_repository is not None
        else InMemoryPublishedClauses(clauses),
        policies,
        explanation_generator,
        timeout_seconds=explanation_timeout_seconds,
    )
    app = FastAPI(
        title="BenefitFinder API",
        description="Youth employment/housing benefit API (bootstrap).",
        version="0.1.0",
        responses={
            "default": {"model": ErrorResponse, "description": "HTTP error"},
            **{status: {"model": ErrorResponse} for status in HTTP_ERROR_RESPONSES},
        },
    )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        # Keep HTTP semantics while replacing detail with a safe public message.
        # Status alone cannot identify domain errors such as unsupported_domain.
        code, message = HTTP_ERROR_RESPONSES.get(
            exc.status_code, ("http_error", "요청을 처리할 수 없습니다.")
        )
        if exc.status_code == 400 and exc.detail == "unsupported_domain":
            code, message = "unsupported_domain", "아직 지원하지 않는 분야입니다."
        if exc.status_code == 503 and exc.detail == "calculation_limit_exceeded":
            code, message = (
                "calculation_limit_exceeded",
                "분석 시간을 초과했습니다. 다시 시도해 주세요.",
            )
        headers = {
            name: value
            for name, value in (exc.headers or {}).items()
            if name.lower()
            not in {"content-type", "content-length", "content-encoding", "transfer-encoding"}
        }
        return JSONResponse(
            status_code=exc.status_code,
            headers=headers,
            content=error_body(code, message),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_body("invalid_request", INVALID_REQUEST_MESSAGE),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content=error_body("internal_error", INTERNAL_ERROR_MESSAGE),
        )

    app.state.recommendation_service = partial(
        recommend_mvp_all_periods,
        evaluate_policies=evaluate_policies,
    )
    app.state.recommendation_explainer = explainer
    cash_benefits = example_benefits(policies)
    services = service_catalog if service_catalog is not None else ServiceCatalog(example_services())
    if services.policy_ids.intersection(cash_benefits):
        raise ValueError("cash_service_policy_id_collision")

    def find_benefit(policy_id: str) -> dict[str, Any] | None:
        return cash_benefits.get(policy_id) or services.detail(policy_id)

    app.state.benefit_lookup = find_benefit
    app.state.service_catalog = services
    policy_names = {policy.policy_id.rsplit(":", 1)[1]: policy.policy_name for policy in policies}
    app.state.simulation_service = partial(
        simulate_mvp,
        recommend=app.state.recommendation_service,
        change_profile=change_profile,
        next_threshold=partial(next_threshold, policy_names=policy_names),
    )

    app.state.region_catalog = region_catalog if region_catalog is not None else OfficialRegionCatalog()
    app.state.analysis_timeout_seconds = analysis_timeout_seconds

    app.include_router(health_router)
    app.include_router(options_router)
    app.include_router(recommendations_router)
    app.include_router(simulations_router)
    app.include_router(benefits_router)
    return app
