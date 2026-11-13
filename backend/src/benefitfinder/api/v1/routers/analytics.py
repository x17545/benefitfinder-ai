"""Privacy-safe analytics configuration and event intake endpoints."""

from types import MappingProxyType

from fastapi import APIRouter, BackgroundTasks, Depends, Response, status

from benefitfinder.api.dependencies.analytics import get_analytics_service
from benefitfinder.api.schemas.analytics import AnalyticsConfigResponse, AnalyticsEventRequest
from benefitfinder.application.analytics import (
    AnalyticsEventRecord,
    AnalyticsParameter,
    AnalyticsService,
)

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


@router.get("/config", response_model=AnalyticsConfigResponse)
def get_analytics_config(
    response: Response,
    service: AnalyticsService = Depends(get_analytics_service),
) -> AnalyticsConfigResponse:
    """Expose only the enabled flag; credentials remain server-side."""
    response.headers["Cache-Control"] = "no-store"
    return AnalyticsConfigResponse(enabled=service.enabled)


@router.post(
    "/events",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def create_analytics_event(
    payload: AnalyticsEventRequest,
    background_tasks: BackgroundTasks,
    service: AnalyticsService = Depends(get_analytics_service),
) -> Response:
    """Accept a validated scalar event; outbound delivery failures remain invisible."""
    parameters: dict[str, AnalyticsParameter] = payload.event.model_dump(
        by_alias=True,
        exclude={"name"},
        exclude_none=True,
    )
    event = AnalyticsEventRecord(
        client_id=payload.client_id,
        session_id=payload.session_id,
        name=payload.event.name,
        parameters=MappingProxyType(parameters),
    )
    background_tasks.add_task(service.deliver, event)
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"Cache-Control": "no-store"})
