
from __future__ import annotations

from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class HousingInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    living: Literal[
        "alone",
        "with_parents",
        "with_family",
        "dorm",
        "other",
    ] | None = None

    home_ownership: Literal[
        "homeless",
        "owned",
        "unknown",
    ] | None = None

    household_head: Literal[
        "head",
        "member",
        "unknown",
    ] | None = None


class HouseholdInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    household_size: Literal[1, 2, 3, 4] | None = None

    parent_income_band: Literal[
        "under_3m",
        "between_3m_5m",
        "between_5m_7m",
        "over_7m",
    ] | None = None

    parent_asset_band: Literal[
        "under_300m",
        "between_300m_500m",
        "over_500m",
    ] | None = None


from pydantic import Field, model_validator


class MilitaryMonthsRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min: int | None = Field(default=None, ge=0)
    max: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_range(self):
        if (
            self.min is not None
            and self.max is not None
            and self.min > self.max
        ):
            raise ValueError("min must be less than or equal to max")
        return self


class HousingContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal[
        "monthly_rent",
        "jeonse",
        "owned",
        "none",
    ] | None = None

    deposit_band: Literal[
        "under_50m",
        "between_50m_100m",
        "between_100m_300m",
        "over_300m",
    ] | None = None

    rent_band: Literal[
        "under_40",
        "between_40_60",
        "between_60_70",
        "over_70",
    ] | None = None


class YouthRegion(BaseModel):
    """시도 지역 코드."""

    model_config = ConfigDict(extra="forbid")

    sido_code: str = Field(min_length=1)


class YouthDomainData(BaseModel):
    """청년 정책 13단계 선택형 입력 데이터."""

    model_config = ConfigDict(extra="forbid")

    military_months_range: MilitaryMonthsRange | None = None

    region: YouthRegion | None = None

    school: Literal[
        "high_school_below",
        "high_school_enrolled",
        "high_school_graduate",
        "university_enrolled",
        "university_expected",
        "university_graduate",
        "masters_doctorate",
    ] | None = None

    marital: Literal[
        "single",
        "married",
        "marrying_soon",
    ] | None = None

    housing: HousingInfo | None = None
    contract: HousingContract | None = None

    asset_band: Literal[
        "under_122m",
        "between_122m_345m",
        "over_345m",
        "unknown",
    ] | None = None

    special: list[Literal[
        "basic_livelihood",
        "near_poverty",
        "single_parent",
        "disabled",
        "sme_employee",
        "farmer_fisher",
        "soldier",
        "none",
    ]] | None = None

    interests: list[Literal[
        "housing",
        "employment",
    ]] | None = None

    received: list[Literal[
        "monthly_rent",
        "youth_account",
        "tomorrow_account",
        "employment_support",
        "jeonse_loan",
        "none",
    ]] | None = None

    household: HouseholdInfo | None = None


    @field_validator("special", "interests", "received")
    @classmethod
    def validate_unique_items(cls, value):
        if value is None:
            return value

        if len(value) != len(set(value)):
            raise ValueError("Duplicate selections are not allowed")

        return value

    @field_validator("special", "received")
    @classmethod
    def validate_none_selection(cls, value):
        if value is None:
            return value

        if "none" in value and len(value) > 1:
            raise ValueError("'none' must be selected alone")

        return value
