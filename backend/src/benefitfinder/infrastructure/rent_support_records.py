"""Narrow executable schema for reviewed household rent-support parameters."""

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RentSupportRule(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    youth_income_max_bps: int = Field(ge=0, le=1000000)
    original_income_max_bps: int = Field(ge=0, le=1000000)
    youth_asset_max_won: int = Field(ge=0, le=2**53 - 1)
    original_asset_max_won: int = Field(ge=0, le=2**53 - 1)
    parent_exempt_min_age: int = Field(ge=0, le=120)
    monthly_cap_won: int = Field(gt=0, le=2**53 - 1)
    maximum_months: int = Field(ge=1, le=24)

    support_starts_month: str = Field(pattern=r"^20[0-9]{2}-(0[1-9]|1[0-2])$")
    support_ends_month: str = Field(pattern=r"^20[0-9]{2}-(0[1-9]|1[0-2])$")

    @model_validator(mode='after')
    def validate_period(self):
        if self.support_starts_month > self.support_ends_month:
            raise ValueError('rent_support_period_reversed')
        return self
