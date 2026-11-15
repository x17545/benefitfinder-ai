"""Optional, per-request facts for local cash-policy assessment."""

from datetime import date
import re
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StrictBool, model_validator

Won = Annotated[int, Field(ge=0, le=2**53 - 1, strict=True)]
Month = Annotated[str, Field(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")]
ProgramId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_:-]{0,79}$")]


def parse_local_date(value) -> date:
    if type(value) is date:
        return value
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("cash_date_must_be_iso_date")
    return date.fromisoformat(value)


LocalDate = Annotated[date, BeforeValidator(parse_local_date)]


class Facts(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProgramPeriod(Facts):
    program_id: ProgramId
    starts_on: LocalDate
    ends_on: LocalDate | None

    @model_validator(mode="after")
    def validate_period(self):
        if self.ends_on is not None and self.ends_on < self.starts_on:
            raise ValueError("program_period_reversed")
        return self


class ExamFacts(Facts):
    exam_date: LocalDate | None = None
    fee_won: Won | None = None
    supported_exam: StrictBool | None = None
    unemployed_on_exam_date: StrictBool | None = None
    same_exam_awards: Annotated[int, Field(ge=0, le=1000, strict=True)] | None = None
    prior_awards_won: Won | None = None
    other_reimbursement: StrictBool | None = None
    other_exclusions: StrictBool | None = None


class RentPayment(Facts):
    month: Month
    paid_on: LocalDate
    amount_won: Won


class RentFacts(Facts):
    deposit_won: Won | None = None
    contract_rent_won: Won | None = None
    health_insurance: Literal["employee", "regional"] | None = None
    health_premium_won: Won | None = None
    applicant_lease: StrictBool | None = None
    other_exclusions: StrictBool | None = None
    paid_months: Annotated[list[RentPayment], Field(max_length=24)] | None = None

    @model_validator(mode="after")
    def validate_paid_months(self):
        months = [entry.month for entry in self.paid_months or ()]
        if len(months) != len(set(months)):
            raise ValueError("duplicate_paid_month")
        return self


class CashFacts(Facts):
    birth_date: LocalDate | None = None
    exam: ExamFacts | None = None
    rent: RentFacts | None = None
    program_periods: Annotated[list[ProgramPeriod], Field(max_length=50)] | None = None
