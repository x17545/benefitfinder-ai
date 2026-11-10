"""Public detail for the same virtual catalogue used by the MVP calculation."""

from typing import Any

from benefitfinder.application.policy_explanations import ExplanationRequest
from benefitfinder.application.recommendation_service import MVP_CONFLICTS, MVP_PAYMENTS
from benefitfinder.application.response_mapper import mvp_policy_id, mvp_policy_source
from benefitfinder.domains.youth.policy_evaluator import AGE_LIMITS, INCOME_LIMITS


def _age_rule(key: str) -> str:
    lower, upper, extended = AGE_LIMITS[key]
    suffix = "이며 가상 군 복무 연장 기준을 적용합니다." if extended else "입니다."
    return f"만 {lower}~{upper}세{suffix}"

_RULES = {
    "P1": {
        "age": _age_rule("P1"),
        "home_ownership": "무주택 청년이 대상입니다.",
        "monthly_rent": "월세 계약이 필요합니다.",
        "income": f"연 소득 {INCOME_LIMITS['P1'] // 10_000:,}만 원 이하입니다.",
    },
    "P3": {
        "age": _age_rule("P3"),
        "employment": "구직 중인 청년이 대상입니다.",
    },
    "P6": {
        "age": _age_rule("P6"),
        "home_ownership": "무주택 청년이 대상입니다.",
        "income": f"연 소득 {INCOME_LIMITS['P6'] // 10_000:,}만 원 이하입니다.",
    },
}


def example_benefits(policies: tuple[ExplanationRequest, ...]) -> dict[str, dict[str, Any]]:
    """Use only the predefined virtual policies; never read drafts or private files."""
    by_id = {policy.policy_id: policy for policy in policies}
    details = {}
    for key, payments in MVP_PAYMENTS.items():
        policy_id = mvp_policy_id(key)
        policy = by_id[policy_id]
        source = mvp_policy_source(key)

        def evidence(text: str, location: str) -> list[dict[str, Any]]:
            return [{"quote": "[가상 예제] " + text, "location": location, "source": source}]

        total = sum(amount for _, amount in payments)
        months = payments[-1][0]
        periodic = key in ("P1", "P3")
        details[policy_id] = {
            "id": policy_id,
            "domain": "youth",
            "title": policy.policy_name,
            "summary": "계산 흐름을 확인하는 가상 정책이에요. 실제 지원금이나 모집을 뜻하지 않아요.",
            "provider": "가상 정책기관",
            "categories": [policy.category],
            "virtual_data": True,
            "application": {"status": "unknown", "starts_on": None, "ends_on": None, "url": None},
            "eligibility_conditions": [
                {"id": condition, "description": rule, "evidence": evidence(rule, "가상 지원 대상")}
                for condition, rule in _RULES[key].items()
            ],
            "benefit_amount": {
                "type": "monthly" if periodic else "lump_sum",
                "amount_won": payments[0][1],
                "period_months": months,
                "max_total_won": total,
                "pay_timing": "monthly_from_start" if periodic else "yearly",
                "amount_text": (
                    f"[가상] {months}개월 동안 월 {payments[0][1] // 10_000}만 원"
                    if periodic else f"[가상] 1년마다 {payments[0][1] // 10_000}만 원, 총 2회"
                ),
            },
            "conflicts": [
                {
                    "policy_id": mvp_policy_id(other),
                    "evidence": evidence(
                        "두 가상 주거 정책을 동시에 받을 수 없다고 가정합니다.", "가상 중복 제한",
                    ),
                }
                for pair in MVP_CONFLICTS if key in pair
                for other in pair if other != key
            ],
            "source": source,
            "domain_data": {},
        }
    return details
