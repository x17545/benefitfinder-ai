"""Optional, per-request facts for local and published cash-policy assessment."""

from datetime import date
import re
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StrictBool, model_validator

Won = Annotated[int, Field(ge=0, le=2**53 - 1, strict=True)]
Month = Annotated[str, Field(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")]
IncomeRatioBps = Annotated[int, Field(ge=0, le=1_000_000, strict=True)]
ProgramId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_:-]{0,79}$")]
SourceCashPolicyId = Annotated[str, Field(pattern=r"^youth:[A-Za-z0-9:_-]{1,100}$")]


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


class RentSupportFacts(Facts):
    """Applicant facts used only to assess rent support; absent values stay unknown."""

    youth_income_ratio_bps: IncomeRatioBps | None = Field(
        default=None,
        description=(
            "공식 심사 기준중위소득 대비 청년가구 비율(bps). "
            "10000=100%이며 개인 연봉 구간으로 추정하지 않는다."
        ),
    )
    original_income_ratio_bps: IncomeRatioBps | None = Field(
        default=None,
        description=(
            "공식 심사 기준중위소득 대비 원가구 비율(bps). "
            "10000=100%이며 개인 연봉 구간으로 추정하지 않는다."
        ),
    )
    youth_assets_won: Won | None = None
    original_assets_won: Won | None = None
    monthly_rent_won: Won | None = None
    housing_benefit_rent_won: Won | None = None
    original_household_exempt: StrictBool | None = Field(
        default=None,
        description=(
            "나이·혼인 외 사유로 기관이 원가구 미고려를 인정했는지. "
            "별도 사유 없음은 false, 미상은 null이다."
        ),
    )
    subscription_account: StrictBool | None = None
    independent_from_parents: StrictBool | None = None
    other_exclusions_clear: StrictBool | None = None
    requested_months: Annotated[int, Field(ge=1, le=24, strict=True)] | None = None
    prior_supported_months: Annotated[int, Field(ge=0, le=24, strict=True)] | None = None
    first_payment_month: Month | None = Field(
        default=None,
        description=(
            "첫 지급예정월(YYYY-MM). 미상이면 계산을 확인 필요로 두며, "
            "미래예정월을 임의로 신청기간 앞당김으로 바꾸지 않는다."
        ),
    )


class SourceCashFacts(Facts):
    """Applicant facts for one published source-cash policy."""

    policy_id: SourceCashPolicyId
    payment_month: Month | None = None
    eligible_cost_won: Won | None = None
    prior_support_won: Won | None = None
    other_reimbursement_won: Won | None = None
    other_exclusions_clear: StrictBool | None = None


class CashFacts(Facts):
    """Optional assessment facts; missing or null values remain unknown."""

    birth_date: LocalDate | None = None
    exam: ExamFacts | None = None
    rent: RentFacts | None = None
    rent_support: RentSupportFacts | None = None
    source_cash: Annotated[list[SourceCashFacts], Field(max_length=100)] | None = None
    program_periods: Annotated[list[ProgramPeriod], Field(max_length=50)] | None = None

    @model_validator(mode="after")
    def validate_source_cash_policy_ids(self):
        policy_ids = [facts.policy_id for facts in self.source_cash or ()]
        if len(policy_ids) != len(set(policy_ids)):
            raise ValueError("duplicate_source_cash_policy_id")
        return self
