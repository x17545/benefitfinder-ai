"""F14 public catalogue, recommendation filtering and evidence-based valuation."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

from benefitfinder.application.recruitment import application_on

MAX_SAFE_WON = 2**53 - 1
Verdict = Literal["met", "not_met", "needs_review"]


class ServiceRecommendations(Protocol):
    def recommend(self, domain: str, profile: Any) -> dict[str, Any]: ...


def korea_today() -> date:
    return datetime.now(timezone(timedelta(hours=9))).date()


@dataclass(frozen=True)
class ServicePrice:
    unit_value_won: int
    basis: str
    url: str | None
    checked_at: date | None
    reviewed: bool

    def total_for(self, quantity: int, today: date) -> int | None:
        """A number without review and a complete reference is not a price."""
        if (
            not self.reviewed
            or type(self.unit_value_won) is not int
            or not 0 <= self.unit_value_won <= MAX_SAFE_WON
            or not self.basis.strip()
            or not self.url
            or any(character.isspace() for character in self.url)
            or not self.url.startswith(("https://", "http://"))
            or self.checked_at is None
            or self.checked_at > today
        ):
            return None
        try:
            if not urlsplit(self.url).hostname:
                return None
        except ValueError:
            return None
        value = self.unit_value_won * quantity
        return value if value <= MAX_SAFE_WON else None


@dataclass(frozen=True)
class ServiceCondition:
    condition_id: str
    quote: str
    evaluate: Callable[[Any], Verdict]
    evidence_items: tuple[dict[str, Any], ...] = ()

    def evidence(self, source: dict[str, str]) -> list[dict[str, Any]]:
        if self.evidence_items:
            return deepcopy(list(self.evidence_items))
        return [{"quote": self.quote, "location": "지원 대상", "source": source.copy()}]


@dataclass(frozen=True)
class ServiceOffer:
    policy_id: str
    title: str
    summary: str
    provider: str
    category: str
    source: dict[str, str]
    application: dict[str, str | None]
    item: str
    unit: str
    quantity: int
    price: ServicePrice | None
    conditions: tuple[ServiceCondition, ...]
    virtual_data: bool = False
    extra_categories: tuple[str, ...] = ()
    conflicts: tuple[dict[str, Any], ...] = ()

    @property
    def categories(self) -> list[str]:
        return [self.category, *self.extra_categories]

    def __post_init__(self) -> None:
        if type(self.quantity) is not int or not 1 <= self.quantity <= MAX_SAFE_WON:
            raise ValueError("invalid_service_quantity")

    def application_on(self, today: date) -> dict[str, str | None]:
        return application_on(self.application, today)

    def public_service(self, today: date) -> dict[str, Any]:
        price = self.price
        value = price.total_for(self.quantity, today) if price is not None else None
        return {
            "item": self.item,
            "unit": self.unit,
            "quantity": self.quantity,
            "unit_value_won": price.unit_value_won if value is not None else None,
            "value_basis": price.basis if value is not None else None,
            "value_source_url": price.url if value is not None else None,
            "value_checked_at": price.checked_at.isoformat() if value is not None else None,
            "value_status": "verified" if value is not None else "unknown",
            "estimated_value_won": value,
        }

    def public_detail(self, today: date) -> dict[str, Any]:
        return {
            "id": self.policy_id,
            "domain": self.policy_id.split(":", 1)[0],
            "title": self.title,
            "summary": self.summary,
            "provider": self.provider,
            "categories": self.categories,
            "application": self.application_on(today),
            "source": self.source.copy(),
            "eligibility_conditions": [
                {"id": rule.condition_id, "description": rule.quote,
                 "evidence": rule.evidence(self.source)}
                for rule in self.conditions
            ],
            "service": self.public_service(today),
            "virtual_data": self.virtual_data,
            "domain_data": {},
            **({'conflicts': deepcopy(list(self.conflicts))} if self.conflicts else {}),
        }

    def diagnosis(self, profile: Any) -> list[dict[str, Any]]:
        return [
            {
                "condition_id": rule.condition_id,
                "status": status,
                "reason": {
                    "met": "입력 조건이 기준에 맞아요.",
                    "not_met": "입력 조건이 기준에 맞지 않아요.",
                    "needs_review": "입력 조건이 없거나 기준 적용을 확인해야 해요.",
                }[status],
                "evidence": rule.evidence(self.source),
            }
            for rule in self.conditions
            for status in (rule.evaluate(profile),)
        ]


class ServiceCatalog:
    def __init__(
        self, offers: tuple[ServiceOffer, ...], *, today: Callable[[], date] = korea_today,
    ) -> None:
        self._offers = {offer.policy_id: offer for offer in offers}
        if len(self._offers) != len(offers):
            raise ValueError("duplicate_service_policy")
        self._today = today

    @property
    def policy_ids(self) -> frozenset[str]:
        return frozenset(self._offers)

    def detail(self, policy_id: str) -> dict[str, Any] | None:
        offer = self._offers.get(policy_id)
        return None if offer is None else offer.public_detail(self._today())

    def recommend(self, domain: str, profile: Any) -> dict[str, Any]:
        """Return an independent, request-local view; never change the cash candidate set."""
        data = profile.domain_data
        interests = data.interests if data is not None else None
        today = self._today()
        rows: list[dict[str, Any]] = []
        virtual_data = False
        for offer in self._offers.values():
            if not offer.policy_id.startswith(domain + ":"):
                continue
            if interests and not set(offer.categories).intersection(interests):
                continue
            application = offer.application_on(today)
            if application["status"] in ("closed", "upcoming"):
                continue
            eligibility = offer.diagnosis(profile)
            if any(condition["status"] == "not_met" for condition in eligibility):
                continue
            virtual_data = virtual_data or offer.virtual_data
            rows.append({
                "policy_id": offer.policy_id,
                "title": offer.title,
                "categories": offer.categories,
                "application": application,
                "source": offer.source.copy(),
                "service": offer.public_service(today),
                "eligibility": eligibility,
            })

        def sort_key(row: dict[str, Any]) -> tuple[bool, int, bool, str]:
            value = row["service"]["estimated_value_won"]
            return (value is None, -(value or 0), row["application"]["status"] != "open",
                    row["policy_id"])

        rows.sort(key=sort_key)
        values = [
            row["service"]["estimated_value_won"] for row in rows
            if row["service"]["estimated_value_won"] is not None
        ]
        total = sum(values)
        return {"services": rows, "services_total_won": total if values and total <= MAX_SAFE_WON else None,
                "virtual_data": virtual_data}
