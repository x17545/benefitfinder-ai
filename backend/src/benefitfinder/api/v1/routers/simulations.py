"""S4 HTTP adapter. All policy math stays outside the router."""

import asyncio
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from benefitfinder.api.dependencies.analysis_usage import reserve_analysis
from benefitfinder.api.schemas.simulation import SimulationRequest

router = APIRouter(prefix="/api/v1", tags=["simulations"])


def get_simulation_service(request: Request) -> Callable[..., dict[str, Any]]:
    return request.app.state.simulation_service


@router.post("/simulations", responses={400: {"description": "Unsupported domain"}})
async def create_simulation(
    request: SimulationRequest,
    http_request: Request,
    simulate: Callable[..., dict[str, Any]] = Depends(get_simulation_service),
) -> dict[str, Any]:
    if request.domain != "youth":
        raise HTTPException(status_code=400, detail="unsupported_domain")
    income = request.income_change
    if income is not None and request.scenario not in ("employed", "income_up"):
        raise HTTPException(status_code=422)
    await reserve_analysis(http_request)
    options = request.options
    try:
        return await asyncio.to_thread(
            simulate,
            profile=request.profile,
            scenario=request.scenario,
            period=(options.period or "1y") if options else "1y",
            present_value=bool(options and options.present_value),
            income_change=(income.from_won, income.to_won) if income else None,
        )
    except ValueError as exc:
        if str(exc) == "inconsistent_income_assumption":
            raise HTTPException(status_code=422) from None
        raise
