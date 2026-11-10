"""GET /api/v1/options의 공용 계약 모델."""

from pydantic import BaseModel, ConfigDict, Field


class OptionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class District(OptionModel):
    code: str = Field(min_length=1)
    name: str = Field(min_length=1)


class Region(OptionModel):
    sido_code: str = Field(min_length=1)
    sido_name: str = Field(min_length=1)
    sigungu: list[District]


class Band(OptionModel):
    value: str
    label: str


class Bands(OptionModel):
    income: list[Band] = Field(min_length=1)
    asset: list[Band] = Field(min_length=1)
    deposit: list[Band] = Field(min_length=1)
    rent: list[Band] = Field(min_length=1)
    parent_income: list[Band] = Field(min_length=1)
    parent_asset: list[Band] = Field(min_length=1)


class SelectOptions(OptionModel):
    regions: list[Region]
    bands: Bands
