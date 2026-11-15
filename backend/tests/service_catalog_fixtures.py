"""Only fictional F14 service records for unit and repository tests."""

import hashlib
from datetime import datetime, timezone
from typing import Any

from benefitfinder.application.published_services import PublishedService
from benefitfinder.infrastructure.service_catalog_records import ServiceCatalogRecord

CHECKED_AT = "2026-11-01T00:00:00+00:00"
PUBLISHED_AT = datetime(2026, 11, 2, tzinfo=timezone.utc)

_AGE_QUOTE = "[가상] 만 19세 이상 39세 이하 청년을 지원합니다."
_SERVICE_QUOTE = "[가상] 면접 정장 대여를 3회까지 지원합니다."
_CONFLICT_QUOTE = "[가상] 청년 취업 지원금 정책과 중복하여 받을 수 없습니다."
_PRICE_QUOTE = "[가상] 면접 정장 1회 대여 시장가격은 40,000원입니다."


def service_source_text() -> str:
    return " ".join((_AGE_QUOTE, _SERVICE_QUOTE, _CONFLICT_QUOTE))


def price_source_text() -> str:
    return _PRICE_QUOTE


def _service_source(key: str) -> dict[str, str]:
    return {
        "url": f"https://service.example.test/notices/{key}",
        "document_version": "[가상] 서비스 공고 v1",
        "checked_at": CHECKED_AT,
    }


def _price_source() -> dict[str, str]:
    return {
        "url": "https://price.example.test/interview-suit-rental",
        "document_version": "[가상] 시장가격 조사 v1",
        "checked_at": CHECKED_AT,
    }


def _evidence(quote: str, location: str, source: dict[str, str]) -> list[dict[str, Any]]:
    return [{"quote": quote, "location": location, "source": source.copy()}]


def service_release_data(key: str = "S", price: bool = True) -> dict[str, Any]:
    source = _service_source(key)
    price_data = (
        {
            "unit_value_won": 40_000,
            "basis": "[가상] 면접 정장 시장 대여료 조사",
            "source": _price_source(),
            "quote": _PRICE_QUOTE,
            "location": "[가상] 시장가격표",
            "source_sha256": hashlib.sha256(price_source_text().encode("utf-8")).hexdigest(),
        }
        if price
        else None
    )
    return {
        "kind": "service",
        "benefit": {
            "id": f"youth:service:{key}",
            "domain": "youth",
            "title": "[가상] 청년 면접 정장 대여",
            "summary": "[가상] 면접 정장을 대여하는 가상 서비스입니다.",
            "provider": "[가상] 고용·주거 지원기관",
            "categories": ["employment", "housing"],
            "application": {
                "status": "open",
                "starts_on": None,
                "ends_on": None,
                "url": f"https://apply.example.test/services/{key}",
            },
            "eligibility_conditions": [
                {
                    "id": "age",
                    "description": "[가상] 만 19~39세 청년",
                    "evidence": _evidence(_AGE_QUOTE, "[가상] 지원 대상", source),
                }
            ],
            "source": source,
            "domain_data": {"region": "[가상] 전국"},
            "virtual_data": True,
            "conflicts": [
                {
                    "policy_id": "youth:catalog:A",
                    "evidence": _evidence(
                        _CONFLICT_QUOTE, "[가상] 중복 지원 제한", source
                    ),
                }
            ],
            "service": {
                "item": "[가상] 면접 정장 대여",
                "unit": "[가상] 회",
                "quantity": 3,
            },
        },
        "conditions": [
            {
                "condition_id": "age",
                "kind": "age",
                "minimum": 19,
                "maximum": 39,
                "military_extension_years": 0,
            }
        ],
        "service_evidence": _evidence(
            _SERVICE_QUOTE, "[가상] 서비스 내용", source
        ),
        "price": price_data,
        "source_sha256": hashlib.sha256(service_source_text().encode("utf-8")).hexdigest(),
    }


class MemoryServices:
    def __init__(self, *records: dict[str, Any]) -> None:
        self.services = tuple(
            ServiceCatalogRecord.model_validate(record).published(PUBLISHED_AT)
            for record in records
        )

    def list_published(self) -> tuple[PublishedService, ...]:
        return self.services

    def get_published(self, policy_id: str) -> PublishedService | None:
        return next(
            (service for service in self.services if service.policy_id == policy_id),
            None,
        )
