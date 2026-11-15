"""Only exact-source, review-digest-bound F13 clauses enter a release."""

from copy import deepcopy
import unittest

from pydantic import ValidationError

from backend.tests.catalog_fixtures import release_data
from backend.tests.release_clause_fixtures import release_with_clauses
from backend.tests.service_catalog_fixtures import service_release_data
from benefitfinder.infrastructure.policy_catalog_records import CatalogRecord, record_hash, validate_source_text
from benefitfinder.infrastructure.service_catalog_records import (
    ServiceCatalogRecord, service_record_hash, validate_service_sources,
)


class ReleaseClausesTest(unittest.TestCase):
    def test_optional_clauses_preserve_legacy_hash_and_are_bound_when_supplied(self) -> None:
        for service, model, digest, legacy in (
            (False, CatalogRecord, record_hash, release_data()),
            (True, ServiceCatalogRecord, service_record_hash, service_release_data(price=False)),
        ):
            with self.subTest(service=service):
                old = model.model_validate(legacy)
                self.assertNotIn("explanation_clauses", old.model_dump())
                legacy["explanation_clauses"] = None
                self.assertEqual(digest(old), digest(model.model_validate(legacy)))
                data, text = release_with_clauses(service=service)
                record = model.model_validate(data)
                check = validate_service_sources if service else validate_source_text
                check(record, text)
                changed = deepcopy(data)
                changed["explanation_clauses"][0]["title"] += " 변경"
                self.assertNotEqual(digest(record), digest(model.model_validate(changed)))

    def test_wrong_source_duplicate_ids_unsafe_topics_and_empty_content_fail_validation(self) -> None:
        mutations = (
            lambda d: d.update(explanation_clauses=[]),
            lambda d: d.update(explanation_clauses=[d["explanation_clauses"][0]] * 201),
            lambda d: d["explanation_clauses"].append(d["explanation_clauses"][0]),
            lambda d: d["explanation_clauses"][0].update(topics=["income"]),
            lambda d: d["explanation_clauses"][0].update(topics=["special"]),
            lambda d: d["explanation_clauses"][0].update(topics=["age", "age"]),
            lambda d: d["explanation_clauses"][0].update(topics=[]),
            lambda d: d["explanation_clauses"][0]["evidence"].update(quote="   "),
            lambda d: d["explanation_clauses"][0]["evidence"]["source"].update(document_version="other"),
        )
        for service, model in ((False, CatalogRecord), (True, ServiceCatalogRecord)):
            for mutation in mutations:
                data, _ = release_with_clauses(service=service)
                mutation(data)
                with self.subTest(service=service, mutation=mutation), self.assertRaises(ValidationError):
                    model.model_validate(data)

    def test_clause_quote_must_exist_in_the_exact_source(self) -> None:
        for service, model, check in (
            (False, CatalogRecord, validate_source_text),
            (True, ServiceCatalogRecord, validate_service_sources),
        ):
            data, text = release_with_clauses(service=service)
            data["explanation_clauses"][0]["evidence"]["quote"] = "지어낸 안내"
            with self.subTest(service=service), self.assertRaisesRegex(ValueError, "quote_missing_from_source"):
                check(model.model_validate(data), text)

    def test_duplicate_conflict_targets_cannot_hide_unmapped_citations(self) -> None:
        for service, model in ((False, CatalogRecord), (True, ServiceCatalogRecord)):
            data, _ = release_with_clauses(service=service)
            conflict = {'policy_id': 'youth:catalog:OTHER',
                        'evidence': [deepcopy(data['explanation_clauses'][0]['evidence'])]}
            data['benefit']['conflicts'] = [conflict, deepcopy(conflict)]
            with self.subTest(service=service), self.assertRaisesRegex(ValueError, 'duplicate_conflict'):
                model.model_validate(data)
