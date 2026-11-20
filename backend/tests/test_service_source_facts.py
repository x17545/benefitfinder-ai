"""Offline tests: service records gain proven residence/age rules and closed status."""

from __future__ import annotations

import hashlib
import unittest
from types import SimpleNamespace

from benefitfinder.application.region_options import DistrictOption, RegionOption
from benefitfinder.domains.youth.published_eligibility import evaluate_condition
from benefitfinder.infrastructure.service_catalog_records import (
    ServiceCatalogRecord,
    validate_service_sources,
)
from benefitfinder.ingestion.service_source_facts import bind_service_source_facts

from backend.tests.service_catalog_fixtures import (
    service_release_data,
    service_source_text,
)

POLICY_NO = "20990101005400000001"
REGIONS = (
    RegionOption("41", "가상북도", (DistrictOption("41110", "가상시"), DistrictOption("41130", "다른시"))),
    RegionOption("48", "가상남도", (DistrictOption("48120", "남쪽시"), DistrictOption("48170", "남쪽군"))),
)
_TARGET_QUOTE = "○ [가상] 청년, 신혼부부 등"
_SERVICE_QUOTE = "○ [가상] 공공임대주택 10호 신축"


def projection(**fields: str) -> str:
    base = {"plcyNo": POLICY_NO, "plcyNm": "[가상] 남도 청년 보금자리",
            "plcySprtCn": _TARGET_QUOTE + "\n" + _SERVICE_QUOTE}
    base.update(fields)
    return "\n\n".join(f"[{key}]\n{value}" for key, value in base.items())


def record(source_text: str, **overrides) -> ServiceCatalogRecord:
    source = {
        "url": "https://www.youthcenter.go.kr/youthPolicy/ythPlcyTotalSearch/ythPlcyDetail/" + POLICY_NO,
        "document_version": "sha256:" + "a" * 64,
        "checked_at": "2026-11-15T00:00:00+00:00",
    }
    data = {
        "kind": "service",
        "benefit": {
            "id": "youth:" + POLICY_NO,
            "domain": "youth",
            "title": "[가상] 남도 청년 보금자리",
            "summary": "[가상] 공공임대주택을 제공합니다.",
            "provider": "[가상] 남도",
            "categories": ["housing"],
            "application": {"status": "unknown", "starts_on": None, "ends_on": None, "url": None},
            "eligibility_conditions": [{
                "id": "youth-housing-target",
                "description": "[가상] 청년·신혼부부",
                "evidence": [{"quote": _TARGET_QUOTE, "location": "plcySprtCn", "source": source}],
            }],
            "source": source,
            "domain_data": {},
            "virtual_data": False,
            "conflicts": [],
            "service": {"item": "[가상] 공공임대주택", "unit": "호", "quantity": 10},
        },
        "conditions": [{"condition_id": "youth-housing-target", "kind": "manual"}],
        "service_evidence": [{"quote": _SERVICE_QUOTE, "location": "plcySprtCn", "source": source}],
        "price": None,
        "source_sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        "collection_sources": [{"source": "youthcenter", "external_id": POLICY_NO,
                                "material_sha256": "a" * 64}],
    }
    data.update(overrides)
    return ServiceCatalogRecord.model_validate(data)


def profile(sido: str | None, age: int | None = 25) -> SimpleNamespace:
    region = SimpleNamespace(sido_code=sido) if sido else None
    return SimpleNamespace(age=age, region_code=None, domain_data=SimpleNamespace(region=region))


def rules_by_id(value: ServiceCatalogRecord) -> dict[str, dict]:
    return {rule.condition_id: rule.model_dump(exclude_none=True) for rule in value.conditions}


class ServiceSourceFactsTest(unittest.TestCase):
    def test_province_limited_service_excludes_other_province(self) -> None:
        text = projection(zipCd="48120,48170")
        facts = bind_service_source_facts(record(text), text, regions=REGIONS)

        rule = rules_by_id(facts.record)["source-facts:selection_domain_data_region_sido_code"]
        self.assertEqual(rule["values"], ["48"])
        self.assertEqual(evaluate_condition(profile("41"), rule), "not_met")
        self.assertEqual(evaluate_condition(profile("48"), rule), "met")
        self.assertEqual(evaluate_condition(profile(None), rule), "needs_review")
        validate_service_sources(facts.record, text, None)

    def test_existing_conditions_are_kept(self) -> None:
        text = projection(zipCd="48120,48170")
        facts = bind_service_source_facts(record(text), text, regions=REGIONS)

        ids = [condition.id for condition in facts.record.benefit.eligibility_conditions]
        self.assertEqual(ids[0], "youth-housing-target")
        self.assertEqual(rules_by_id(facts.record)["youth-housing-target"]["kind"], "manual")

    def test_nationwide_service_gets_no_region_rule(self) -> None:
        text = projection(zipCd="41110,41130,48120,48170")
        facts = bind_service_source_facts(record(text), text, regions=REGIONS)

        self.assertFalse(facts.changed)
        self.assertEqual(facts.record, record(text))

    def test_complete_suwon_district_group_uses_parent_city_code(self) -> None:
        text = projection(zipCd="41111,41113,41115,41117")
        facts = bind_service_source_facts(record(text), text, regions=REGIONS)

        rule = rules_by_id(facts.record)["source-facts:selection_region_code"]
        self.assertEqual(rule["values"], ["41110"])
        city = SimpleNamespace(age=25, region_code="41110", domain_data=SimpleNamespace(region=None))
        other = SimpleNamespace(age=25, region_code="41130", domain_data=SimpleNamespace(region=None))
        self.assertEqual(evaluate_condition(city, rule), "met")
        self.assertEqual(evaluate_condition(other, rule), "not_met")

    def test_partial_suwon_district_group_adds_no_region_rule(self) -> None:
        text = projection(zipCd="41111,41113")
        facts = bind_service_source_facts(record(text), text, regions=REGIONS)

        self.assertFalse(facts.changed)

    def test_nationwide_list_with_district_codes_gets_no_region_rule(self) -> None:
        text = projection(zipCd="41111,41113,41115,41117,41130,48120,48170")
        facts = bind_service_source_facts(record(text), text, regions=REGIONS)

        self.assertFalse(facts.changed)

    def test_age_rule_is_added_from_structured_fields(self) -> None:
        text = projection(sprtTrgtAgeLmtYn="N", sprtTrgtMinAge="19", sprtTrgtMaxAge="39")
        facts = bind_service_source_facts(record(text), text, regions=REGIONS)

        rule = rules_by_id(facts.record)["source-facts:age"]
        self.assertEqual((rule["minimum"], rule["maximum"]), (19, 39))
        self.assertEqual(evaluate_condition(profile("41", age=25), rule), "met")
        self.assertEqual(evaluate_condition(profile("41", age=40), rule), "not_met")

    def test_closed_code_marks_application_closed(self) -> None:
        text = projection(aplyPrdSeCd="0057003")
        facts = bind_service_source_facts(record(text), text, regions=REGIONS)

        self.assertTrue(facts.closed)
        self.assertEqual(facts.record.benefit.application.status, "closed")

    def test_existing_region_rule_is_not_duplicated(self) -> None:
        text = projection(zipCd="48120,48170")
        base = record(text)
        data = base.model_dump(mode="json")
        data["benefit"]["eligibility_conditions"].append({
            "id": "region", "description": "[가상] 남도",
            "evidence": [{"quote": _TARGET_QUOTE, "location": "plcySprtCn",
                          "source": data["benefit"]["source"]}],
        })
        data["conditions"].append({"condition_id": "region", "kind": "selection",
                                   "field": "domain_data.region.sido_code", "values": ["48"]})
        facts = bind_service_source_facts(ServiceCatalogRecord.model_validate(data), text, regions=REGIONS)

        self.assertFalse(facts.changed)

    def test_source_hash_mismatch_is_rejected(self) -> None:
        text = projection(zipCd="48120,48170")
        with self.assertRaisesRegex(ValueError, "source_hash_mismatch"):
            bind_service_source_facts(record(text), text + "\n", regions=REGIONS)

    def test_virtual_record_is_left_unchanged(self) -> None:
        virtual = ServiceCatalogRecord.model_validate(service_release_data(price=False))
        facts = bind_service_source_facts(virtual, service_source_text(), regions=REGIONS)

        self.assertIs(facts.record, virtual)
        self.assertFalse(facts.changed)


if __name__ == "__main__":
    unittest.main()
