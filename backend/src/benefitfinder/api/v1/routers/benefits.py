"""Read-only S5 policy detail; no user profile is needed."""

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

router = APIRouter(prefix="/api/v1", tags=["benefits"])


def get_benefit_lookup(request: Request) -> Callable[[str], dict[str, Any] | None]:
    return request.app.state.benefit_lookup


@router.get("/benefits/{benefit_id}")
def read_benefit(
    benefit_id: str, lookup: Callable[[str], dict[str, Any] | None] = Depends(get_benefit_lookup),
) -> dict[str, Any]:
    benefit = lookup(benefit_id)
    if benefit is None:
        raise HTTPException(status_code=404)
    return benefit
