"""Read public data/rate/usage metadata; this endpoint never starts analysis."""

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from benefitfinder.api.schemas.meta import MetaResponse
from benefitfinder.application.service_meta import ReadServiceMetadata, ServiceMetadataUnavailable

router = APIRouter(prefix="/api/v1", tags=["meta"])


def get_metadata_reader(request: Request) -> ReadServiceMetadata:
    return request.app.state.read_service_metadata


@router.get("/meta", response_model=MetaResponse)
def get_meta(
    response: Response,
    read: ReadServiceMetadata = Depends(get_metadata_reader),
) -> MetaResponse:
    try:
        snapshot = read()
    except ServiceMetadataUnavailable as error:
        raise HTTPException(status_code=503, headers={"Retry-After": "60"}) from error
    response.headers["Cache-Control"] = "no-store"
    return MetaResponse(**asdict(snapshot))
