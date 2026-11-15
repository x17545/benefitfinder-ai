"""Calculate both S4 views with the same engine as recommendations."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from benefitfinder.application.policy_amounts import amount_metadata
from benefitfinder.application.recommendation_service import MVP_ANNUAL_RATE, MVP_PAYMENTS
from benefitfinder.application.response_mapper import mvp_policy_id
from benefitfinder.domain.present_value import calculate_support_value


def _first(calculation: dict[str, Any], period: str, pv: bool) -> tuple[int, list[str]]:
    view = calculation["results"][period]["pv_on" if pv else "pv_off"]
    if view["calculation_status"] != "available":
        raise ValueError("unavailable_simulation_basis")
    portfolios = view["portfolios"]
    return (portfolios[0]["total_won"], portfolios[0]["members"]) if portfolios else (0, [])


def simulate_mvp(
    *,
    profile: Any,
    scenario: str,
    period: str,
    present_value: bool,
    income_change: tuple[int, int] | None,
    recommend: Callable[..., dict[str, Any]],
    change_profile: Callable[..., tuple[Any, Any, tuple[int, int] | None, str]],
    next_threshold: Callable[..., str | None],
    annual_rate: float | None = MVP_ANNUAL_RATE,
) -> dict[str, Any]:
    annual_before_profile, after_profile, income, scenario_note = change_profile(
        profile, scenario, income_change,
    )
    def calculate(value: Any) -> dict[str, Any]:
        return recommend(
            SimpleNamespace(domain="youth", profile=value, options=None),
            annual_rate=annual_rate,
        )

    before = calculate(profile)
    after = calculate(after_profile)
    before_total, before_members = _first(before, period, present_value)
    after_total, after_members = _first(after, period, present_value)
    annual_baseline = before if income is None else calculate(annual_before_profile)
    annual_before, annual_before_members = _first(annual_baseline, "1y", False)
    annual_after, annual_after_members = _first(after, "1y", False)
    threshold = next_threshold(after_profile, scenario, after["eligibility"])
    rate = None
    if income is not None and income[1] > income[0]:
        rate = (annual_before - annual_after) / (income[1] - income[0]) * 100

    def metadata(calculation: dict[str, Any], basis: str, members: list[str]):
        if "catalog" not in calculation:
            return {}
        catalog = calculation["catalog"]
        return amount_metadata(
            catalog["payments"], catalog["maturity_accruals"], basis, members,
        )

    def totals_metadata(old, new, basis, old_members, new_members):
        result = {}
        for prefix, calculation, members in (
            ("before", old, old_members), ("after", new, new_members),
        ):
            detail = metadata(calculation, basis, members)
            if detail:
                result[prefix + "_amount_breakdown"] = detail["amount_breakdown"]
        return result

    def changes(
        old: list[str], new: list[str], comparison: dict[str, Any], *, gained: bool,
    ) -> list[dict[str, Any]]:
        rows = []
        for key in new:
            if key in old:
                continue
            other_status = comparison["eligibility"][key]["status"]
            published = "catalog" in before
            if gained:
                reason = (
                    ("조건이 바뀌어 계산 후보에 들어왔어요." if published else "가상 조건이 바뀌어 계산 후보에 들어왔어요.")
                    if other_status == "not_met"
                    else "새 조합에 포함됐어요. 새 자격을 얻었다는 뜻은 아니에요."
                )
            else:
                reason = (
                    ("바뀐 조건에서 정책의 자격 기준을 벗어났어요." if published else "바뀐 조건에서 가상 정책의 자격 기준을 벗어났어요.")
                    if other_status == "not_met"
                    else "새 조합에서 빠졌어요. 자격을 잃었다는 뜻은 아니에요."
                )
            schedule = before["catalog"]["payments"] if published else MVP_PAYMENTS
            # Lost policies are in before; gained policies are in after.
            if published and key not in schedule:
                schedule = after["catalog"]["payments"]
            calculation = (after if gained else before)
            accruals = calculation["catalog"]["maturity_accruals"] if published else {}
            rows.append({
                "policy_id": key if published else mvp_policy_id(key),
                "reason": reason,
                "amount_won": calculate_support_value(
                    schedule[key], period, annual_rate if present_value else None,
                    maturity_accruals=accruals.get(key),
                ),
                **metadata(calculation, period, [key]),
            })
        return rows

    return {
        "scenario": scenario,
        "basis_period": period,
        "virtual_data": before["virtual_data"] or after["virtual_data"],
        "before_total_won": before_total,
        "after_total_won": after_total,
        **totals_metadata(before, after, period, before_members, after_members),
        "annual": {
            "before_won": annual_before, "after_won": annual_after,
            **totals_metadata(
                annual_baseline, after, "1y", annual_before_members, annual_after_members,
            ),
        },
        "income_change": None if income is None else {
            "from_won": income[0],
            "to_won": income[1],
            "assumption_note": "소득 구간의 가상 대표값이에요. 실제 연 소득을 확인한 값이 아니에요.",
        },
        "effective_marginal_rate": rate,
        "lost": changes(after_members, before_members, after, gained=False),
        "gained": changes(before_members, after_members, before, gained=True),
        "next_threshold": None if threshold is None else {"text": threshold},
        "todo": None,
        "calculation_note": (
            "총액은 선택 기간, 소득과 지원금 순증가는 대표값을 적용한 1년 명목금액 기준이에요. "
            "세금·4대 보험은 빼고 지원금 변화만 계산했어요."
            + (
                " 만기형은 명목 합계·연간 지원금에 미지급 예상 적립액을 포함하고, "
                "현재가치 합계에는 선택 기간 안에 지급되는 현금만 반영해요. 만기 시 지급돼요."
                if any(c.get("catalog", {}).get("maturity_accruals") for c in (before, after))
                else ""
            )
            + (" " + scenario_note if scenario_note else "")
        ),
    }
