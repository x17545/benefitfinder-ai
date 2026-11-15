"""Approval fingerprints include explicit source identities, never title guesses."""

import hashlib
import json
import unittest

from pydantic import ValidationError

from backend.tests.catalog_fixtures import release_data
from benefitfinder.infrastructure.policy_catalog_records import CatalogRecord, record_hash


class CatalogSourceBindingsTest(unittest.TestCase):
    def test_legacy_fictional_digest_is_preserved(self) -> None:
        record = CatalogRecord.model_validate(release_data())
        legacy = record.model_dump(mode="json")
        legacy.pop("collection_sources")
        digest = hashlib.sha256(json.dumps(
            legacy, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()).hexdigest()
        self.assertEqual(record_hash(record), digest)
        self.assertEqual(record_hash(CatalogRecord.model_validate_json(record.model_dump_json())), digest)

    def test_real_releases_require_explicit_links_bound_to_approval(self) -> None:
        data = release_data()
        data["benefit"]["virtual_data"] = False
        with self.assertRaises(ValidationError):
            CatalogRecord.model_validate(data)
        data["collection_sources"] = [{
            "source": "youthcenter", "external_id": "fictional-independent-source-id",
            "material_sha256": "a" * 64,
        }]
        original = CatalogRecord.model_validate(data)
        data["collection_sources"][0]["material_sha256"] = "b" * 64
        self.assertNotEqual(record_hash(original), record_hash(CatalogRecord.model_validate(data)))

    def test_invalid_duplicate_or_oversized_links_fail(self) -> None:
        link = {"source": "youthcenter", "external_id": "fictional-id", "material_sha256": "a" * 64}
        cases = [
            [{**link, "source": "unsupported"}], [{**link, "external_id": " "}],
            [{**link, "external_id": "a\0b"}], [{**link, "external_id": "a" * 201}],
            [{**link, "material_sha256": "bad"}], [{**link, "extra": "not-accepted"}],
            [link, link], [{**link, "external_id": str(i)} for i in range(21)], None,
        ]
        for links in cases:
            with self.subTest(links=links), self.assertRaises(ValidationError):
                CatalogRecord.model_validate({**release_data(), "collection_sources": links})
