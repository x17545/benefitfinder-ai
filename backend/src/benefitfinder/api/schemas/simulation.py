"""HTTP input for the S4 simulation contract."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from benefitfinder.api.schemas.recommendation import RecommendationRequest


class IncomeChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_won: int = Field(ge=0, strict=True)
    to_won: int = Field(ge=0, strict=True)


class SimulationRequest(RecommendationRequest):
    scenario: Literal["employed", "income_up", "independent", "age_plus_one"]
    income_change: IncomeChange | None = None
