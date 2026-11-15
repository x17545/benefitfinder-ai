"""Local catalogue assessment. Individual estimates never become published portfolios."""

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any

KST = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class LocalCashPolicy:
    detail: dict[str, Any]
    rule: dict[str, Any]
    region_code: str


class LocalCashRecommendations:
    def __init__(
        self,
        policies: tuple[LocalCashPolicy, ...],
        evaluate: Callable,
        *,
        reference_at: datetime | None = None,
    ):
        self.policies = {p.detail["id"]: p for p in policies}
        self.updated_at = max(p.detail["source"]["checked_at"] for p in policies)
        self.evaluate = evaluate
        self.reference_at = reference_at

    def now(self) -> datetime:
        return self.reference_at or datetime.now(KST)

    def detail(self, policy_id: str, now: datetime | None = None) -> dict[str, Any] | None:
        policy = self.policies.get(policy_id)
        if policy is None:
            return None
        now = (now or self.now()).astimezone(KST)
        detail = deepcopy(policy.detail)
        application = detail["application"]
        if application["status"] in {"closed", "unknown"}:
            return detail
        end = datetime.fromisoformat(policy.rule["application_ends_at"])
        if now > end:
            application["status"] = "closed"
            return detail

        starts_at = policy.rule.get("application_starts_at")
        starts_on = application["starts_on"]
        if starts_at is not None:
            start = datetime.fromisoformat(starts_at)
        elif starts_on is not None:
            start = datetime.combine(datetime.fromisoformat(starts_on).date(), time.min, KST)
        else:
            # No date to compare: retain the explicitly stored open/upcoming status.
            return detail
        application["status"] = "upcoming" if now < start else "open"
        return detail

    def __call__(self, request, annual_rate: float | None = None) -> dict[str, Any]:
        now = self.now().astimezone(KST)
        details, diagnoses, eligibility, payments, amounts = {}, {}, {}, {}, {}
        for key, policy in self.policies.items():
            if request.profile.region_code != policy.region_code:
                continue
            interests = getattr(request.profile.domain_data, "interests", None)
            if interests and not set(interests).intersection(policy.detail["categories"]):
                continue
            detail = self.detail(key, now)
            assert detail is not None
            details[key] = detail
            checks, amount = self.evaluate(request.profile, policy.rule, now.date())
            statuses = set(checks.values())
            status = (
                "not_met"
                if "not_met" in statuses
                else "needs_review"
                if "needs_review" in statuses or amount is None
                else "met"
            )
            eligibility[key] = {"status": status, "conditions": checks}
            diagnoses[key] = [
                {
                    "condition_id": condition["id"],
                    "status": checks[condition["id"]],
                    "evidence": condition["evidence"],
                }
                for condition in detail["eligibility_conditions"]
            ]
            if status == "met" and amount is not None and detail["application"]["status"] == "open":
                amounts[key] = amount
                # Membership only is used for the local fallback path; no payment date is inferred.
                payments[key] = ()
        nominal = {
            "calculation_status": "available",
            "unavailable_reason": None,
            "portfolios": [],
            "amounts": amounts,
        }
        pv = {
            "calculation_status": "unavailable",
            "unavailable_reason": "unknown_payment_timing",
            "portfolios": [],
        }
        return {
            "domain": "youth",
            "virtual_data": True,
            "eligibility": eligibility,
            "results": {period: {"pv_off": nominal, "pv_on": pv} for period in ("1y", "2y", "all")},
            "catalog": {
                "details": details,
                "diagnosis": diagnoses,
                "conflicts": [],
                "unverified": [],
                "payments": payments,
                "updated_at": self.updated_at,
                "individual_only": True,
            },
        }
