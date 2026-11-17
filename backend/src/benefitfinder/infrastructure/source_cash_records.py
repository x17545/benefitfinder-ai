"""Narrow executable schema for reviewed applicant-specific cash claims."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


MAX_WON = 2**53 - 1


class SourceCashRule(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    policy_id: str = Field(pattern=r"^youth:[A-Za-z0-9:_-]{1,100}$")
    mode: Literal["fixed", "expense"]
    amount_won: int = Field(gt=0, le=MAX_WON)
    reimbursement_bps: int = Field(ge=1, le=10000)

    @model_validator(mode="after")
    def validate_fixed_reimbursement(self) -> "SourceCashRule":
        if self.mode == "fixed" and self.reimbursement_bps != 10000:
            raise ValueError("fixed_source_cash_requires_full_reimbursement")
        return self


__all__ = ["MAX_WON", "SourceCashRule"]
