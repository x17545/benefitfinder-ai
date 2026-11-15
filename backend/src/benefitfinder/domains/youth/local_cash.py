"""Deterministic rules for disclosed, applicant-specific cash reimbursements."""

from datetime import date
from typing import Any


def verdict(value: bool | None) -> str:
    return "needs_review" if value is None else "met" if value else "not_met"


def all_checks(*values: bool | None) -> bool | None:
    return False if False in values else None if None in values else True


def compare(value, predicate):
    return None if value is None else predicate(value)


def no_overlap(facts, programs: list[str], day: date | None) -> bool | None:
    if day is None or facts.program_periods is None:
        return None
    return not any(
        item.program_id in programs
        and item.starts_on <= day
        and (item.ends_on is None or day <= item.ends_on)
        for item in facts.program_periods
    )


def assess_cash(profile, rule: dict[str, Any], today: date) -> tuple[dict[str, str], int | None]:
    data = profile.domain_data
    facts = getattr(data, "cash_facts", None)
    if rule["kind"] == "exam_reimbursement":
        exam = getattr(facts, "exam", None)
        birth = getattr(facts, "birth_date", None)
        day = getattr(exam, "exam_date", None)
        checks = {
            "age": compare(
                birth, lambda value: rule["birth_year_min"] <= value.year <= rule["birth_year_max"]
            ),
            "exam": all_checks(
                compare(
                    day,
                    lambda value: (
                        date.fromisoformat(rule["exam_starts_on"])
                        <= value
                        <= min(today, date.fromisoformat(rule["exam_ends_on"]))
                    ),
                ),
                getattr(exam, "supported_exam", None),
                compare(getattr(exam, "fee_won", None), lambda value: value > 0),
                compare(
                    getattr(exam, "same_exam_awards", None),
                    lambda value: value < rule["same_exam_limit"],
                ),
            ),
            "employment": getattr(exam, "unemployed_on_exam_date", None),
            "중복 수급": all_checks(
                no_overlap(facts, rule["excluded_programs"], day) if facts else None,
                compare(getattr(exam, "other_reimbursement", None), lambda value: not value),
            ),
            "other_exclusions": compare(
                getattr(exam, "other_exclusions", None), lambda value: not value
            ),
        }
        amount = (
            min(exam.fee_won, max(0, rule["total_cap_won"] - exam.prior_awards_won))
            if exam is not None and exam.fee_won is not None and exam.prior_awards_won is not None
            else None
        )
    else:
        rent = getattr(facts, "rent", None)
        housing = getattr(data, "housing", None)
        contract = getattr(data, "contract", None)
        household = getattr(data, "household", None)
        paid = getattr(rent, "paid_months", None)
        insurance = getattr(rent, "health_insurance", None)
        checks = {
            "age": compare(
                getattr(facts, "birth_date", None),
                lambda value: (
                    date.fromisoformat(rule["birth_date_min"])
                    <= value
                    <= date.fromisoformat(rule["birth_date_max"])
                ),
            ),
            "household": all_checks(
                compare(getattr(household, "household_size", None), lambda value: value == 1),
                compare(getattr(data, "marital", None), lambda value: value == "single"),
                compare(getattr(housing, "living", None), lambda value: value == "alone"),
                compare(
                    getattr(housing, "home_ownership", None),
                    lambda value: None if value == "unknown" else value == "homeless",
                ),
                compare(
                    getattr(housing, "household_head", None),
                    lambda value: None if value == "unknown" else value == "head",
                ),
            ),
            "income": compare(
                getattr(rent, "health_premium_won", None),
                lambda value: value <= rule["health_premium_caps"][insurance],
            )
            if insurance
            else None,
            "monthly_rent": all_checks(
                compare(getattr(contract, "type", None), lambda value: value == "monthly_rent"),
                getattr(rent, "applicant_lease", None),
                compare(
                    getattr(rent, "deposit_won", None),
                    lambda value: value <= rule["deposit_cap_won"],
                ),
                compare(
                    getattr(rent, "contract_rent_won", None),
                    lambda value: value <= rule["contract_rent_cap_won"],
                ),
            ),
            "중복 수급": no_overlap(facts, rule["excluded_programs"], today) if facts else None,
            "other_exclusions": compare(
                getattr(rent, "other_exclusions", None), lambda value: not value
            ),
        }
        amount = (
            None
            if paid is None
            else sum(
                min(entry.amount_won, rule["monthly_cap_won"])
                for entry in paid
                if entry.month in rule["covered_months"] and entry.paid_on <= today
            )
        )
    if amount == 0:
        checks["exam" if rule["kind"] == "exam_reimbursement" else "monthly_rent"] = False
    checks["region"] = True  # The application filters by exact district before evaluating.
    return {key: verdict(value) for key, value in checks.items()}, amount
