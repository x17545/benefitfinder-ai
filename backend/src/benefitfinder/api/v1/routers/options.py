"""공식 지역 목록과 선택 구간을 제공한다."""

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request

from benefitfinder.api.schemas.options import SelectOptions
from benefitfinder.application.region_options import (
    OPTION_BANDS,
    RegionCatalog,
    RegionOptionsUnavailable,
)

router = APIRouter(prefix="/api/v1", tags=["options"])


def get_region_catalog(request: Request) -> RegionCatalog:
    """조립 지점이 주입한 원천을 연결한다."""
    return request.app.state.region_catalog


@router.get("/options", response_model=SelectOptions)
def get_options(catalog: RegionCatalog = Depends(get_region_catalog)) -> SelectOptions:
    """목록 불가 시 공통 503을 반환하며 가상값/만료된 코드를 섞지 않는다."""
    try:
        regions = catalog.get_regions()
        if not regions:
            raise RegionOptionsUnavailable("빈 지역 목록")
    except RegionOptionsUnavailable as error:
        raise HTTPException(status_code=503, headers={"Retry-After": "60"}) from error
    return SelectOptions.model_validate(
        {
            "regions": [asdict(region) for region in regions],
            "bands": {
                key: [{"value": value, "label": label} for value, label in choices]
                for key, choices in OPTION_BANDS.items()
            },
        }
    )
