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

NOT_FOUND_MESSAGE = "요청한 리소스를 찾을 수 없습니다."
INVALID_REQUEST_MESSAGE = "요청 값이 올바르지 않습니다."
INTERNAL_ERROR_MESSAGE = "내부 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."


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
        responses={status: {"model": ErrorResponse} for status in (404, 405, 422, 500)},
    )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        if exc.status_code == 404:
            return JSONResponse(
                status_code=404,
                content=error_body("not_found", NOT_FOUND_MESSAGE),
            )
        if exc.status_code == 405:
            return JSONResponse(
                status_code=405,
                headers=exc.headers,
                content=error_body("method_not_allowed", "지원하지 않는 HTTP 메서드입니다."),
            )
        return JSONResponse(
            status_code=500,
            content=error_body("internal_error", INTERNAL_ERROR_MESSAGE),
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
    return app
