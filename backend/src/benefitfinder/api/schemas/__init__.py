"""HTTP request and response models following the shared contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

HealthStatus = Literal["ok"]

ErrorCode = Literal[
    "http_error",
    "invalid_request",
    "unsupported_domain",
    "not_found",
    "method_not_allowed",
    "dependency_unavailable",
    "internal_error",
    "daily_limit_exceeded",
    "calculation_limit_exceeded",
]


class HealthResponse(BaseModel):
    """GET /health response body (liveness only, no DB readiness)."""

    model_config = ConfigDict(extra="forbid")

    status: HealthStatus = Field(description="Fixed liveness marker.")


class ErrorResponse(BaseModel):
    """Common error body matching contracts/v1/error.schema.json."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, description="Unique identifier of this error response.")
    code: ErrorCode = Field(description="Machine-readable lower_snake_case error code.")
    message: str = Field(min_length=1, description="Human-readable message without PII or internals.")
