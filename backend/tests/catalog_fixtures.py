"""Only fictional releases for unit and dedicated PostgreSQL tests."""

import hashlib
from datetime import datetime, timezone
from typing import Any

from benefitfinder.infrastructure.policy_catalog_records import CatalogRecord


def source_text(amount: int = 1_000_000) -> str:
    return f"[가상] 만 19~34세를 지원합니다. 연 소득 3천만 원 이하. 지원금 {amount:,}원을 1개월 뒤 지급합니다. 두 정책은 중복 불가입니다."


SOURCE_TEXT = source_text()
CHECKED_AT = "2026-11-01T00:00:00+00:00"


def release_data(key: str = "A", *, amount: int = 1_000_000) -> dict[str, Any]:
    source = {
        "url": f"https://catalog.example/{key}",
        "document_version": "fictional-v1",
        "checked_at": CHECKED_AT,
    }

    def evidence(quote: str) -> list[dict[str, Any]]:
        return [{"quote": quote, "location": "가상 안내", "source": source.copy()}]

    return {
        "benefit": {
            "id": f"youth:catalog:{key}",
            "domain": "youth",
            "title": f"[가상] 정책 {key}",
            "summary": "게시·계산 검증용 가상 정책",
            "provider": "가상 기관",
            "categories": ["housing"],
            "application": {"status": "open", "starts_on": None, "ends_on": None, "url": None},
            "eligibility_conditions": [
                {
                    "id": "age",
                    "description": "만 19~34세",
                    "evidence": evidence("만 19~34세를 지원합니다."),
                }
            ],
            "source": source,
            "domain_data": {},
            "reviewed": True,
            "virtual_data": True,
            "benefit_amount": {
                "type": "lump_sum",
                "amount_won": amount,
                "period_months": 1,
                "max_total_won": amount,
                "pay_timing": "once",
                "amount_status": "verified",
            },
        },
        "conditions": [
            {
                "condition_id": "age",
                "kind": "age",
                "minimum": 19,
                "maximum": 34,
                "military_extension_years": 0,
            }
        ],
        "payments": [{"month": 1, "amount_won": amount}],
        "payment_evidence": evidence(f"지원금 {amount:,}원을 1개월 뒤 지급합니다."),
        "unresolved_combination_rules": False,
        "source_sha256": hashlib.sha256(source_text(amount).encode()).hexdigest(),
    }


class MemoryPolicies:
    def __init__(self, *records: dict[str, Any]) -> None:
        self.policies = tuple(
            CatalogRecord.model_validate(record).published(
                datetime(2026, 11, 2, tzinfo=timezone.utc),
            )
            for record in records
        )
        self.reads = 0

    def list_published(self):
        self.reads += 1
        return self.policies

    def get_published(self, policy_id: str):
        return next((policy for policy in self.policies if policy.policy_id == policy_id), None)
