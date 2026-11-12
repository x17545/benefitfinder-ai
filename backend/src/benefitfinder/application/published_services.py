"""Published non-cash offers using the same reviewed youth eligibility evaluator."""

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol

from benefitfinder.application.service_catalog import (
    ServiceCatalog, ServiceCondition, ServiceOffer, ServicePrice, korea_today,
)


@dataclass(frozen=True)
class PublishedService:
    detail: dict[str, Any]
    conditions: tuple[dict[str, Any], ...]
    price: ServicePrice | None
    published_at: str

    @property
    def policy_id(self) -> str:
        return self.detail['id']


class PublishedServiceRepository(Protocol):
    def list_published(self) -> tuple[PublishedService, ...]: ...
    def get_published(self, policy_id: str) -> PublishedService | None: ...


class PublishedServices:
    def __init__(self, repository: PublishedServiceRepository,
                 evaluate: Callable[[Any, dict[str, Any]], str]) -> None:
        self.repository = repository
        self.evaluate = evaluate

    def _offer(self, release: PublishedService) -> ServiceOffer:
        detail = release.detail
        rules = {rule['condition_id']: rule for rule in release.conditions}
        conditions = tuple(ServiceCondition(
            condition['id'], condition['description'],
            lambda profile, rule=rules[condition['id']]: self.evaluate(profile, rule),
            tuple(condition['evidence']),
        ) for condition in detail['eligibility_conditions'])
        content = detail['service']
        return ServiceOffer(
            policy_id=release.policy_id, title=detail['title'], summary=detail['summary'],
            provider=detail['provider'], category=detail['categories'][0],
            source=detail['source'], application=detail['application'],
            item=content['item'], unit=content['unit'], quantity=content['quantity'],
            price=release.price, conditions=conditions, virtual_data=detail['virtual_data'],
            extra_categories=tuple(detail['categories'][1:]), conflicts=tuple(detail['conflicts']),
        )

    @property
    def policy_ids(self) -> frozenset[str]:
        return frozenset(release.policy_id for release in self.repository.list_published())

    def detail(self, policy_id: str) -> dict[str, Any] | None:
        release = self.repository.get_published(policy_id)
        if release is None:
            return None
        detail = self._offer(release).public_detail(korea_today())
        if 'application_guide' in release.detail:
            detail['application_guide'] = deepcopy(release.detail['application_guide'])
        return detail

    def recommend(self, domain: str, profile: Any) -> dict[str, Any]:
        offers = tuple(self._offer(release) for release in self.repository.list_published())
        return ServiceCatalog(offers).recommend(domain, profile)
