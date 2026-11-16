"""Reviewed rent-support rules; no salary-band or missing-value imputation."""

from datetime import date
from typing import Any


def _status(value: bool | None) -> str:
    return 'needs_review' if value is None else 'met' if value else 'not_met'


def assess_rent_support(profile: Any, rule: dict[str, Any]) -> dict[str, str]:
    data = getattr(profile, 'domain_data', None)
    facts = getattr(getattr(data, 'cash_facts', None), 'rent_support', None)

    def maximum(field: str, limit: int) -> bool | None:
        value = getattr(facts, field, None)
        return None if value is None else value <= limit

    age = getattr(profile, 'age', None)
    marital = getattr(data, 'marital', None)
    recognized = getattr(facts, 'original_household_exempt', None)
    if (age is not None and age >= rule['parent_exempt_min_age']
            or marital == 'married' or recognized is True):
        exempt = True
    elif age is not None and marital in {'single', 'marrying_soon'} and recognized is False:
        exempt = False
    else:
        exempt = None

    def original(field: str, limit: int) -> bool | None:
        check = maximum(field, limit)
        if exempt is True or check is True:
            return True
        return check if exempt is False else None

    requested = getattr(facts, 'requested_months', None)
    prior = getattr(facts, 'prior_supported_months', None)
    rent = getattr(facts, 'monthly_rent_won', None)
    benefit = getattr(facts, 'housing_benefit_rent_won', None)
    first = getattr(facts, 'first_payment_month', None)
    period = None
    if first is not None and requested is not None:
        def index(month: str) -> int:
            year, number = map(int, month.split('-'))
            return year * 12 + number - 1
        period = (index(rule['support_starts_month']) <= index(first)
                  and index(first) + requested - 1 <= index(rule['support_ends_month']))
    checks = {
        'youth_income': maximum('youth_income_ratio_bps', rule['youth_income_max_bps']),
        'youth_assets': maximum('youth_assets_won', rule['youth_asset_max_won']),
        'original_income': original('original_income_ratio_bps', rule['original_income_max_bps']),
        'original_assets': original('original_assets_won', rule['original_asset_max_won']),
        'subscription_account': getattr(facts, 'subscription_account', None),
        'independent_from_parents': getattr(facts, 'independent_from_parents', None),
        'other_exclusions_clear': getattr(facts, 'other_exclusions_clear', None),
        'duration': (None if requested is None or prior is None else
                     1 <= requested <= rule['maximum_months'] - prior),
        'payment_period': period,
        'rent_payment': None if rent is None else rent > 0,
        'housing_benefit_deduction': None if benefit is None else True,
    }
    return {key: _status(value) for key, value in checks.items()}


def rent_support_payments(profile: Any, policy: Any, today: date) -> tuple[tuple[int, int], ...] | None:
    """Resolve applicant-dependent payments, keeping the stored cap out of totals."""
    rules = [item['rent_support'] for item in policy.conditions if item['kind'] == 'rent_support']
    if not rules:
        return policy.payments
    if len(rules) != 1:
        return None
    rule = rules[0]
    if set(assess_rent_support(profile, rule).values()) != {'met'}:
        return None
    facts = profile.domain_data.cash_facts.rent_support
    # Deduct the monthly-rent portion only, after limiting the reimbursable rent.
    first = getattr(facts, 'first_payment_month', None)
    if first is None:
        return None
    year, month = map(int, first.split('-'))
    first_index = year * 12 + month - 1
    offset = first_index - (today.year * 12 + today.month - 1)
    start_year, start_month = map(int, rule['support_starts_month'].split('-'))
    end_year, end_month = map(int, rule['support_ends_month'].split('-'))
    if (offset < 0 or first_index < start_year * 12 + start_month - 1
            or first_index + facts.requested_months - 1 > end_year * 12 + end_month - 1):
        return None
    monthly = max(0, min(facts.monthly_rent_won, rule['monthly_cap_won'])
                  - facts.housing_benefit_rent_won)
    return tuple((offset + month, monthly) for month in range(facts.requested_months))
