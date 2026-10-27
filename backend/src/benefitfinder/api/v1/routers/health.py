"""Liveness probe router."""

from __future__ import annotations

from fastapi import APIRouter

from benefitfinder.api.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
def get_health() -> HealthResponse:
    """Return liveness only; a 200 here does not guarantee DB readiness."""
    return HealthResponse(status="ok")
