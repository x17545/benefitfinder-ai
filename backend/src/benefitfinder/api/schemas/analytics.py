"""Strict HTTP schemas for the fixed privacy-safe analytics contract."""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

_STEP = Annotated[int, Field(ge=1, le=13)]
_RANK = Annotated[int, Field(ge=1, le=3)]
_POLICY_ID_PATTERN = re.compile(r"[A-Za-z0-9:_-]{1,100}")


def _validate_policy_id(value: str) -> str:
    if _POLICY_ID_PATTERN.fullmatch(value) is None:
        raise ValueError("invalid_policy_id")
    return value


_POLICY_ID = Annotated[str, Field(min_length=1, max_length=100), AfterValidator(_validate_policy_id)]


class _StrictAnalyticsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _AnalyticsEventBase(_StrictAnalyticsModel):
    pass


class ConsentCheckEvent(_AnalyticsEventBase):
    name: Literal["consent_check"]


class RememberOnEvent(_AnalyticsEventBase):
    name: Literal["remember_on"]


class StepViewEvent(_AnalyticsEventBase):
    name: Literal["step_view"]
    step: _STEP


class StepCompleteEvent(_AnalyticsEventBase):
    name: Literal["step_complete"]
    step: _STEP


class StepSkipEvent(_AnalyticsEventBase):
    name: Literal["step_skip"]


class AnalyzeStartEvent(_AnalyticsEventBase):
    name: Literal["analyze_start"]


class AnalyzeSuccessEvent(_AnalyticsEventBase):
    name: Literal["analyze_success"]
    duration_ms: Annotated[int, Field(ge=0, le=120_000)]
    portfolio_count: Annotated[int, Field(ge=0, le=3)]


class AnalyzeFailEvent(_AnalyticsEventBase):
    name: Literal["analyze_fail"]
    code: Literal[
        "http_error",
        "invalid_request",
        "unsupported_domain",
        "not_found",
        "method_not_allowed",
        "daily_limit_exceeded",
        "dependency_unavailable",
        "internal_error",
        "calculation_limit_exceeded",
        "timeout",
        "network_error",
        "invalid_response",
        "unknown",
    ]


class PeriodChangeEvent(_AnalyticsEventBase):
    name: Literal["period_change"]
    period: Literal["1y", "2y", "all"]


class PresentValueToggleEvent(_AnalyticsEventBase):
    name: Literal["pv_toggle"]
    on: bool


class ComboSelectEvent(_AnalyticsEventBase):
    name: Literal["combo_select"]
    rank: _RANK


class DetailViewEvent(_AnalyticsEventBase):
    name: Literal["detail_view"]
    policy_id: _POLICY_ID


class ApplyClickEvent(_AnalyticsEventBase):
    name: Literal["apply_click"]
    policy_id: _POLICY_ID
    from_: Literal["result", "detail"] = Field(alias="from")
    rank: _RANK | None


class WhatIfOpenEvent(_AnalyticsEventBase):
    name: Literal["whatif_open"]


class WhatIfSelectEvent(_AnalyticsEventBase):
    name: Literal["whatif_select"]
    scenario: Literal["employed", "income_up", "independent", "age_plus_one"]


class ImageSaveEvent(_AnalyticsEventBase):
    name: Literal["image_save"]


class ClearMineEvent(_AnalyticsEventBase):
    name: Literal["clear_mine"]


AnalyticsEvent = Annotated[
    ConsentCheckEvent
    | RememberOnEvent
    | StepViewEvent
    | StepCompleteEvent
    | StepSkipEvent
    | AnalyzeStartEvent
    | AnalyzeSuccessEvent
    | AnalyzeFailEvent
    | PeriodChangeEvent
    | PresentValueToggleEvent
    | ComboSelectEvent
    | DetailViewEvent
    | ApplyClickEvent
    | WhatIfOpenEvent
    | WhatIfSelectEvent
    | ImageSaveEvent
    | ClearMineEvent,
    Field(discriminator="name"),
]


class AnalyticsEventRequest(_StrictAnalyticsModel):
    """The copied analytics-event contract with strict scalar and event validation."""

    client_id: str = Field(
        min_length=36,
        max_length=36,
        pattern=(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
            r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
        ),
    )
    session_id: Annotated[int, Field(ge=1, le=9_999_999_999)]
    event: AnalyticsEvent


class AnalyticsConfigResponse(_StrictAnalyticsModel):
    """Expose only whether analytics is enabled."""

    enabled: bool
