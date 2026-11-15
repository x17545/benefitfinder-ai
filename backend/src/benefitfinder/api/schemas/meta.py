"""Public metadata matching contracts/v1/meta.schema.json."""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class MetaResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    data_updated_at: AwareDatetime
    base_rate: float | None = Field(ge=0)
    base_rate_status: Literal["verified", "unknown"]
    daily_remaining: int = Field(ge=0, le=100, strict=True)
    virtual_data: bool = False

    @model_validator(mode="after")
    def consistent_rate_status(self) -> "MetaResponse":
        if self.base_rate is None and self.base_rate_status == "verified":
            raise ValueError("inconsistent_rate_status")
        return self
