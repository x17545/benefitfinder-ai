"""Reject unreviewed or unbound F14 service records and publish exact prices."""

from copy import deepcopy
from datetime import date, datetime, timezone
import unittest
from urllib.parse import urlsplit

from pydantic import ValidationError

from backend.tests.service_catalog_fixtures import (
    MemoryServices,
    price_source_text,
    service_release_data,
    service_source_text,
)
from benefitfinder.application.published_services import PublishedService
from benefitfinder.infrastructure.policy_catalog_records import MAX_WON
from benefitfinder.infrastructure.service_catalog_records import (
    ServiceCatalogRecord,
    service_record_hash,
    validate_service_sources,
)


class ServiceCatalogRecordTests(unittest.TestCase):
    def test_fictional_fixture_round_trip_hash_and_metadata(self) -> None:
        data = service_release_data()
        record = ServiceCatalogRecord.model_validate(data)
        self.assertEqual(record.kind, "service")
        self.assertEqual(record.benefit.categories, ["employment", "housing"])
        self.assertEqual(record.benefit.service.quantity, 3)
        self.assertIsNotNone(record.price)
        assert record.price is not None
        self.assertEqual(record.price.unit_value_won, 40_000)
        self.assertEqual(record.conditions[0].minimum, 19)
        self.assertEqual(record.conditions[0].maximum, 39)
        self.assertEqual(record.benefit.conflicts[0].policy_id, "youth:catalog:A")

        document_versions = [
            record.benefit.source.document_version,
            record.service_evidence[0].source.document_version,
            record.price.source.document_version,
        ]
        public_text = [
            record.benefit.title,
            record.benefit.summary,
            record.benefit.provider,
            record.benefit.eligibility_conditions[0].description,
            record.benefit.service.item,
            record.benefit.service.unit,
            record.price.basis,
            record.price.quote,
            record.price.location,
            service_source_text(),
            price_source_text(),
            *document_versions,
        ]
        for value in public_text:
            with self.subTest(value=value):
                self.assertIn("[가상]", value)
        for source in (
            record.benefit.source,
            record.service_evidence[0].source,
            record.price.source,
        ):
            self.assertTrue(urlsplit(source.url).hostname.endswith(".example.test"))
            self.assertTrue(source.checked_at.startswith("2026-11-01"))

        digest = service_record_hash(record)
        restored = ServiceCatalogRecord.model_validate_json(record.model_dump_json())
        self.assertEqual(service_record_hash(restored), digest)
        self.assertRegex(digest, r"^[0-9a-f]{64}$")

        changed = deepcopy(data)
        changed["benefit"]["domain_data"]["fixture_revision"] = 2
        self.assertNotEqual(
            service_record_hash(ServiceCatalogRecord.model_validate(changed)),
            digest,
        )
        linked = deepcopy(data)
        linked["collection_sources"] = [
            {
                "source": "youthcenter",
                "external_id": "fictional-service-source",
                "material_sha256": "a" * 64,
            }
        ]
        self.assertNotEqual(
            service_record_hash(ServiceCatalogRecord.model_validate(linked)),
            digest,
        )

    def test_published_record_uses_service_price_and_code_calculation(self) -> None:
        record = ServiceCatalogRecord.model_validate(service_release_data())
        published = record.published(datetime(2026, 11, 2, 4, 5, tzinfo=timezone.utc))
        self.assertIsInstance(published, PublishedService)
        self.assertEqual(published.detail, record.benefit.model_dump(mode="json"))
        self.assertEqual(
            published.conditions,
            tuple(rule.model_dump(exclude_none=True) for rule in record.conditions),
        )
        self.assertEqual(published.published_at, "2026-11-02T04:05:00+00:00")
        self.assertIsNotNone(published.price)
        assert published.price is not None
        self.assertEqual(published.price.checked_at, date(2026, 11, 1))
        self.assertTrue(published.price.reviewed)
        self.assertEqual(
            published.price.total_for(record.benefit.service.quantity, date(2026, 11, 12)),
            120_000,
        )

        unpriced = ServiceCatalogRecord.model_validate(service_release_data(price=False))
        self.assertIsNone(unpriced.published(datetime(2026, 11, 2, tzinfo=timezone.utc)).price)

    def test_source_binding_checks_body_price_hashes_and_exact_quotes(self) -> None:
        record = ServiceCatalogRecord.model_validate(service_release_data())
        validate_service_sources(record, service_source_text(), price_source_text())

        with self.assertRaisesRegex(ValueError, "source_hash_mismatch"):
            validate_service_sources(record, service_source_text() + " 변조", price_source_text())
        altered_quote = deepcopy(service_release_data())
        altered_quote["service_evidence"][0]["quote"] = "[가상] 공고에 없는 문장"
        with self.assertRaisesRegex(ValueError, "quote_missing_from_source"):
            validate_service_sources(
                ServiceCatalogRecord.model_validate(altered_quote),
                service_source_text(),
                price_source_text(),
            )
        with self.assertRaisesRegex(ValueError, "price_source_required"):
            validate_service_sources(record, service_source_text())
        with self.assertRaisesRegex(ValueError, "price_source_hash_mismatch"):
            validate_service_sources(
                record,
                service_source_text(),
                price_source_text() + " 변조",
            )
        altered_price_quote = deepcopy(service_release_data())
        altered_price_quote["price"]["quote"] = "[가상] 가격 문서에 없는 문장"
        with self.assertRaisesRegex(ValueError, "price_quote_missing_from_source"):
            validate_service_sources(
                ServiceCatalogRecord.model_validate(altered_price_quote),
                service_source_text(),
                price_source_text(),
            )

        unpriced = ServiceCatalogRecord.model_validate(service_release_data(price=False))
        validate_service_sources(unpriced, service_source_text())
        with self.assertRaisesRegex(ValueError, "unexpected_price_source_text"):
            validate_service_sources(unpriced, service_source_text(), price_source_text())

    def test_real_service_requires_explicit_unique_collection_sources(self) -> None:
        data = service_release_data()
        data["benefit"]["virtual_data"] = False
        with self.assertRaisesRegex(
            ValidationError, "real_service_requires_collection_sources"
        ):
            ServiceCatalogRecord.model_validate(data)

        link = {
            "source": "youthcenter",
            "external_id": "fictional-real-service",
            "material_sha256": "a" * 64,
        }
        data["collection_sources"] = [link]
        ServiceCatalogRecord.model_validate(data)
        data["collection_sources"] = [link, link]
        with self.assertRaisesRegex(ValidationError, "duplicate_collection_source"):
            ServiceCatalogRecord.model_validate(data)
        data["collection_sources"] = [
            {**link, "external_id": f"fictional-real-service-{index}"}
            for index in range(21)
        ]
        with self.assertRaises(ValidationError):
            ServiceCatalogRecord.model_validate(data)

    def test_cash_or_unknown_exchange_fields_are_rejected(self) -> None:
        mutations = (
            lambda data: data.update(payments=[]),
            lambda data: data.update(reviewed=True),
            lambda data: data["benefit"].update(benefit_amount={"amount_won": 1}),
            lambda data: data["benefit"].update(reviewed=True),
            lambda data: data["benefit"]["service"].update(amount_won=1),
            lambda data: data["price"].update(reviewed=True),
            lambda data: data.pop("kind"),
            lambda data: data.update(kind="cash"),
            lambda data: data.pop("price"),
        )
        for mutate in mutations:
            data = service_release_data()
            mutate(data)
            with self.subTest(mutate=mutate), self.assertRaises(ValidationError):
                ServiceCatalogRecord.model_validate(data)

    def test_condition_categories_evidence_and_self_conflicts_are_rejected(self) -> None:
        cases = (
            (
                "duplicate condition evidence id",
                lambda data: data["benefit"]["eligibility_conditions"].append(
                    deepcopy(data["benefit"]["eligibility_conditions"][0])
                ),
                "condition_rule_mismatch",
            ),
            (
                "duplicate executable rule",
                lambda data: data["conditions"].append(deepcopy(data["conditions"][0])),
                "condition_rule_mismatch",
            ),
            (
                "rule target mismatch",
                lambda data: data["conditions"][0].update(condition_id="housing"),
                "condition_rule_mismatch",
            ),
            (
                "duplicate category",
                lambda data: data["benefit"].update(categories=["housing", "housing"]),
                "duplicate_category",
            ),
            (
                "condition source mismatch",
                lambda data: data["benefit"]["eligibility_conditions"][0]["evidence"][0][
                    "source"
                ].update(document_version="[가상] 다른 공고"),
                "evidence_source_mismatch",
            ),
            (
                "service evidence source mismatch",
                lambda data: data["service_evidence"][0]["source"].update(
                    document_version="[가상] 다른 공고"
                ),
                "evidence_source_mismatch",
            ),
            (
                "conflict source mismatch",
                lambda data: data["benefit"]["conflicts"][0]["evidence"][0]["source"].update(
                    document_version="[가상] 다른 공고"
                ),
                "evidence_source_mismatch",
            ),
            (
                "self conflict",
                lambda data: data["benefit"]["conflicts"][0].update(
                    policy_id=data["benefit"]["id"]
                ),
                "self_conflict",
            ),
        )
        for label, mutate, error in cases:
            data = service_release_data()
            mutate(data)
            with self.subTest(case=label), self.assertRaisesRegex(ValidationError, error):
                ServiceCatalogRecord.model_validate(data)

    def test_price_and_quantity_reject_floats_booleans_and_bounds(self) -> None:
        mutations = (
            lambda data: data["price"].update(unit_value_won=40_000.0),
            lambda data: data["price"].update(unit_value_won=True),
            lambda data: data["price"].update(unit_value_won=-1),
            lambda data: data["price"].update(unit_value_won=MAX_WON + 1),
            lambda data: data["price"].update(basis="   "),
            lambda data: data["benefit"]["service"].update(quantity=3.0),
            lambda data: data["benefit"]["service"].update(quantity=True),
            lambda data: data["benefit"]["service"].update(quantity=0),
            lambda data: data["benefit"]["service"].update(quantity=MAX_WON + 1),
        )
        for mutate in mutations:
            data = service_release_data()
            mutate(data)
            with self.subTest(mutate=mutate), self.assertRaises(ValidationError):
                ServiceCatalogRecord.model_validate(data)

    def test_memory_repository_returns_published_records_and_computes_totals(self) -> None:
        repository = MemoryServices(
            service_release_data("S1"),
            service_release_data("S2", price=False),
        )
        records = repository.list_published()
        self.assertIsInstance(records, tuple)
        self.assertEqual(len(records), 2)
        self.assertEqual(repository.get_published("youth:service:S1"), records[0])
        self.assertEqual(repository.get_published("youth:service:S2"), records[1])
        self.assertIsNone(repository.get_published("youth:service:missing"))
        self.assertIsNotNone(records[0].price)
        assert records[0].price is not None
        self.assertEqual(records[0].price.total_for(3, date(2026, 11, 12)), 120_000)
        self.assertIsNone(records[1].price)


if __name__ == "__main__":
    unittest.main()
