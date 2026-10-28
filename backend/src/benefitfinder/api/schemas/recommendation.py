
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from benefitfinder.api.schemas.youth_profile import YouthDomainData


class RecommendationOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period: Literal["1y", "2y", "all"] | None = None
    present_value: bool | None = None



class UserProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    age: int | None = Field(default=None, ge=0)
    region_code: str | None = Field(default=None, min_length=1)

    employment_status: Literal[
        "high_school",
        "student",
        "grad_student",
        "job_seeker",
        "employed",
        "short_term_worker",
        "self_employed",
        "freelancer",
        "founder",
        "farmer",
        "other",
    ] | None = None

    income_band: Literal[
        "none",
        "under_15m",
        "between_15m_30m",
        "between_30m_50m",
        "over_50m",
        "unknown",
    ] | None = None

    household_type: str | None = Field(default=None, min_length=1)

    domain_data: YouthDomainData | None = None



class RecommendationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    profile: UserProfile
    options: RecommendationOptions | None = None
