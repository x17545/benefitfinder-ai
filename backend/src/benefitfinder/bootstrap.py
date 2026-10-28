"""Concrete application assembly.

Only this module and the ASGI entrypoint (``main.py``) may wire concrete
pieces together. Routers, schemas, and dependencies never import adapters
directly; they receive already-assembled objects here.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from benefitfinder.api.schemas import ErrorCode, ErrorResponse
from benefitfinder.api.v1.routers.health import router as health_router
from benefitfinder.api.v1.routers.recommendations import router as recommendations_router

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
    return ErrorResponse(
        request_id=str(uuid.uuid4()), code=code, message=message
    ).model_dump()


def create_app() -> FastAPI:
    """Assemble and return a new FastAPI application.

    Each call returns an independent instance; routes added to one instance
    never affect another. The app starts without DB, LLM, or secrets.
    """
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
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        # Keep HTTP semantics while replacing detail with a safe public message.
        # Status alone cannot identify domain errors such as unsupported_domain.
        code, message = HTTP_ERROR_RESPONSES.get(
            exc.status_code, ("http_error", "요청을 처리할 수 없습니다.")
        )
        headers = {
            name: value
            for name, value in (exc.headers or {}).items()
            if name.lower() not in {
                "content-type", "content-length", "content-encoding", "transfer-encoding"
            }
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

    app.include_router(health_router)
    app.include_router(recommendations_router)
    return app
