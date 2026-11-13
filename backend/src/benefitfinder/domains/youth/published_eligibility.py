"""Conservative code evaluation of the supported, reviewed youth conditions."""

from __future__ import annotations

from typing import Any

INCOME_BANDS = {
    "none": (0, 0),
    "under_15m": (1, 14_999_999),
    "between_15m_30m": (15_000_000, 30_000_000),
    "between_30m_50m": (30_000_001, 50_000_000),
    "over_50m": (50_000_001, None),
}


def evaluate_condition(profile: Any, rule: dict[str, Any]) -> str:
    kind = rule["kind"]
    if kind == "age":
        age = profile.age
        if age is None:
            return "needs_review"
        if age < rule["minimum"]:
            return "not_met"
        if age <= rule["maximum"]:
            return "met"
        cap = rule["military_extension_years"]
        if cap == 0 or age > rule["maximum"] + cap:
            return "not_met"
        data = profile.domain_data
        military = getattr(data, "military_months_range", None)
        if military is None or military.min is None:
            return "needs_review"
        lower = min(cap, military.min // 12)
        upper = cap if military.max is None else min(cap, military.max // 12)
        if age <= rule["maximum"] + lower:
            return "met"
        if age > rule["maximum"] + upper:
            return "not_met"
        return "needs_review"
    if kind == "income_max":
        exact = getattr(profile, "income_won", None)
        band = INCOME_BANDS.get(profile.income_band)
        if exact is not None:
            band = (exact, exact)
        if band is None:
            return "needs_review"
        lower, upper = band
        maximum = rule["maximum"] - (0 if rule["inclusive"] else 1)
        if upper is not None and upper <= maximum:
            return "met"
        return "not_met" if lower > maximum else "needs_review"
    if kind in ("selection", "none_of"):
        value = profile
        for part in rule["field"].split("."):
            value = getattr(value, part, None)
        if value is None or value == "unknown" or value == []:
            return "needs_review"
        selected = set(value) if isinstance(value, list) else {value}
        matches = bool(selected.intersection(rule["values"]))
        satisfied = not matches if kind == "none_of" else matches
        return "met" if satisfied else "not_met"
    return "needs_review"
