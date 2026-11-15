"""Read and validate the opt-in local cash catalogue without publishing it."""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from benefitfinder.application.local_cash_recommendations import LocalCashPolicy
from benefitfinder.infrastructure.policy_catalog_records import MAX_WON, Benefit

__all__ = ["load_local_cash_catalog"]

_MAX_JSON_BYTES = 1048576
_MAX_POLICIES = 50
_LOCAL_ID_PREFIX = "youth:local:"
_REGION_RE = "^[0-9]{5}$"
_MONTH_RE = "^(19|20)[0-9]{2}-(0[1-9]|1[0-2])$"
_KST = ZoneInfo("Asia/Seoul")

_REGION_PATTERN = re.compile(_REGION_RE)
_ProgramId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_:-]{0,79}$")]
_MONTH_PATTERN = re.compile(_MONTH_RE)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def _reject_duplicate_keys(pairs):
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError("duplicate_key:" + str(key))
        seen[key] = value
    return seen


def _parse_tz_aware(value, field):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        raise ValueError(field + "_requires_iso_datetime_with_tz") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(field + "_requires_timezone")
    return parsed


def _parse_day(value, field):
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError(field + "_requires_YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(field + "_requires_YYYY-MM-DD") from exc


class LocalRule(_Strict):
    excluded_programs: list[_ProgramId] = Field(max_length=50)
    application_starts_at: str | None = None
    application_ends_at: str

    @model_validator(mode="after")
    def validate_shared(self):
        if len(set(self.excluded_programs)) != len(self.excluded_programs):
            raise ValueError("duplicate_excluded_program")
        end = _parse_tz_aware(self.application_ends_at, "application_ends_at")
        if (
            self.application_starts_at is not None
            and _parse_tz_aware(self.application_starts_at, "application_starts_at") > end
        ):
            raise ValueError("application_window_reversed")
        return self


class HealthPremiumCaps(_Strict):
    employee: int = Field(ge=0, le=MAX_WON)
    regional: int = Field(ge=0, le=MAX_WON)


class ExamReimbursementRule(LocalRule):
    kind: Literal["exam_reimbursement"]
    exam_starts_on: str
    exam_ends_on: str
    total_cap_won: int = Field(ge=0, le=MAX_WON)
    same_exam_limit: int = Field(ge=1, le=100)
    birth_year_min: int = Field(ge=1900, le=2100)
    birth_year_max: int = Field(ge=1900, le=2100)

    @model_validator(mode="after")
    def _validate_exam(self):
        if _parse_day(self.exam_starts_on, "exam_starts_on") > _parse_day(
            self.exam_ends_on, "exam_ends_on"
        ):
            raise ValueError("exam_dates_reversed")
        if self.birth_year_min > self.birth_year_max:
            raise ValueError("birth_year_range_reversed")
        return self


class RentReimbursementRule(LocalRule):
    kind: Literal["rent_reimbursement"]
    covered_months: list[str] = Field(min_length=1, max_length=12)
    monthly_cap_won: int = Field(ge=0, le=MAX_WON)
    deposit_cap_won: int = Field(ge=0, le=MAX_WON)
    contract_rent_cap_won: int = Field(ge=0, le=MAX_WON)
    health_premium_caps: HealthPremiumCaps
    birth_date_min: str
    birth_date_max: str

    @model_validator(mode="after")
    def _validate_rent(self):
        if len(self.covered_months) * self.monthly_cap_won > MAX_WON:
            raise ValueError("rent_total_overflow")
        if len(set(self.covered_months)) != len(self.covered_months):
            raise ValueError("duplicate_covered_month")
        for month in self.covered_months:
            if not isinstance(month, str) or _MONTH_PATTERN.match(month) is None:
                raise ValueError("covered_month_requires_YYYY-MM")
        if sorted(self.covered_months) != list(self.covered_months):
            raise ValueError("covered_months_must_be_sorted")
        if _parse_day(self.birth_date_min, "birth_date_min") > _parse_day(
            self.birth_date_max, "birth_date_max"
        ):
            raise ValueError("birth_date_range_reversed")
        return self


def _kst_day(iso_ts):
    return _parse_tz_aware(iso_ts, "application_timestamp").astimezone(_KST).date().isoformat()


def _expected_ids(kind):
    if kind == "exam_reimbursement":
        return ["age", "region", "exam", "employment", "중복 수급", "other_exclusions"]
    return ["age", "region", "household", "income", "monthly_rent", "중복 수급", "other_exclusions"]


def _check_benefit(benefit, kind, index):
    where = "policies[" + str(index) + "]"
    if not benefit.id.startswith(_LOCAL_ID_PREFIX):
        raise ValueError(where + ": benefit.id must start with youth:local:...")
    if benefit.reviewed is not False:
        raise ValueError(where + ": reviewed must be false (no approval in local JSON)")
    if list(benefit.conflicts) != []:
        raise ValueError(where + ": conflicts must be empty in local JSON")
    ids = [c.id for c in benefit.eligibility_conditions]
    if len(set(ids)) != len(ids):
        raise ValueError(where + ": duplicate_condition_id")
    if set(ids) != set(_expected_ids(kind)):
        raise ValueError(
            where + ": condition ids must be exactly " + ",".join(sorted(_expected_ids(kind)))
        )
    for cond in benefit.eligibility_conditions:
        for item in cond.evidence:
            if item.source != benefit.source:
                raise ValueError(where + ": evidence_source_mismatch")


def _check_application_dates(benefit, rule, index):
    where = "policies[" + str(index) + "]"
    ends_on = benefit.application.ends_on
    if ends_on is None:
        raise ValueError(where + ": application.ends_on is required to match application_ends_at")
    if _kst_day(rule.application_ends_at) != ends_on:
        raise ValueError(where + ": application_ends_at date (Korea) must match ends_on")
    if rule.application_starts_at is not None:
        starts_on = benefit.application.starts_on
        if starts_on is None:
            raise ValueError(
                where + ": application.starts_on is required when application_starts_at is set"
            )
        if _kst_day(rule.application_starts_at) != starts_on:
            raise ValueError(where + ": application_starts_at date (Korea) must match starts_on")


def load_local_cash_catalog(path: str) -> tuple[LocalCashPolicy, ...]:
    target = Path(path)
    with target.open("rb") as stream:
        raw = stream.read(_MAX_JSON_BYTES + 1)
    if len(raw) > _MAX_JSON_BYTES:
        raise ValueError("local_cash_catalog_too_large")
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("local_cash_catalog_requires_utf8") from exc
    try:
        data = json.loads(decoded, object_pairs_hook=_reject_duplicate_keys)
    except (ValueError, RecursionError) as exc:
        raise ValueError("invalid_local_cash_catalog") from exc
    if not isinstance(data, dict) or set(data.keys()) != {"policies"}:
        raise ValueError("catalog root must be exactly policies list")
    policies = data["policies"]
    if not isinstance(policies, list):
        raise ValueError("policies must be a list")
    if not 1 <= len(policies) <= _MAX_POLICIES:
        raise ValueError("too_many_policies: at most 50")
    seen_ids = set()
    out = []
    for index, entry in enumerate(policies):
        where = "policies[" + str(index) + "]"
        if not isinstance(entry, dict) or set(entry.keys()) != {"benefit", "region_code", "rule"}:
            raise ValueError(where + ": entry must be exactly benefit, region_code, rule")
        try:
            benefit = Benefit.model_validate(entry["benefit"])
        except ValueError as exc:
            raise ValueError(where + ": invalid benefit") from exc
        region_code = entry["region_code"]
        if not isinstance(region_code, str) or _REGION_PATTERN.match(region_code) is None:
            raise ValueError(where + ": region_code must be five digits")
        rule_raw = entry["rule"]
        if not isinstance(rule_raw, dict):
            raise ValueError(where + ": rule must be an object")
        kind = rule_raw.get("kind")
        try:
            if kind == "exam_reimbursement":
                rule = ExamReimbursementRule.model_validate(rule_raw)
            elif kind == "rent_reimbursement":
                rule = RentReimbursementRule.model_validate(rule_raw)
            else:
                raise ValueError(
                    where + ": rule.kind must be exam_reimbursement or rent_reimbursement"
                )
        except ValueError as exc:
            raise ValueError(where + ": invalid rule") from exc
        _check_benefit(benefit, str(kind), index)
        _check_application_dates(benefit, rule, index)
        if benefit.id in seen_ids:
            raise ValueError(where + ": duplicate policy id " + benefit.id)
        seen_ids.add(benefit.id)
        outward = benefit.model_copy(update={"virtual_data": True})
        detail = outward.model_dump(mode="json")
        if detail.get("reviewed") is not False or detail.get("virtual_data") is not True:
            raise ValueError(where + ": outward copy flags invalid")
        rule_dict = rule.model_dump(mode="json")
        out.append(LocalCashPolicy(detail=detail, rule=rule_dict, region_code=region_code))
    return tuple(out)
