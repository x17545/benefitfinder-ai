"""Fictional cash/service releases with exact-source F13 clauses."""

from copy import deepcopy
from typing import Any

from backend.tests.application_guide_fixtures import with_guide
from backend.tests.catalog_fixtures import release_data, source_text
from backend.tests.service_catalog_fixtures import service_release_data, service_source_text


def release_with_clauses(key: str = "A", *, service: bool = False) -> tuple[dict[str, Any], str]:
    data, text = with_guide(
        service_release_data(key, price=False) if service else release_data(key),
        service_source_text() if service else source_text(),
    )
    citations = (
        ("age", "[가상] 지원 대상", data["benefit"]["eligibility_conditions"][0]["evidence"][0]),
        ("application", "[가상] 신청 방법", data["benefit"]["application_guide"]["method"]),
        ("amount", "[가상] 지원 내용", (data["service_evidence"] if service else data["payment_evidence"])[0]),
    )
    data["explanation_clauses"] = [
        {"id": topic, "title": title, "evidence": deepcopy(citation), "topics": [topic]}
        for topic, title, citation in citations
    ]
    return data, text


def change_version(data: dict[str, Any], version: str) -> dict[str, Any]:
    result = deepcopy(data)

    def walk(value: object) -> None:
        if isinstance(value, dict):
            if "document_version" in value:
                value["document_version"] = version
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(result)
    return result
