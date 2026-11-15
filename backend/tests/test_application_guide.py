"""F07 exact-source validation, approval compatibility and public HTTP delivery."""

from copy import deepcopy
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.tests.application_guide_fixtures import METHOD, with_guide
from backend.tests.catalog_fixtures import MemoryPolicies, release_data, source_text
from backend.tests.service_catalog_fixtures import (
    MemoryServices, service_release_data, service_source_text,
)
from backend.tests.test_published_catalog import validator
from benefitfinder.bootstrap import create_app
from benefitfinder.infrastructure.database.repositories.published_policies import publish_policy
from benefitfinder.infrastructure.policy_catalog_records import (
    CatalogRecord, record_hash, validate_source_text,
)
from benefitfinder.infrastructure.service_catalog_records import (
    ServiceCatalogRecord, service_record_hash, validate_service_sources,
)


class ApplicationGuideTest(unittest.TestCase):
    def cases(self):
        return (
            (CatalogRecord, record_hash, release_data(), source_text()),
            (ServiceCatalogRecord, service_record_hash, service_release_data(price=False), service_source_text()),
        )

    def test_absent_and_null_extension_preserve_approved_hash_and_public_shape(self):
        for model, digest, data, _ in self.cases():
            with self.subTest(model=model):
                old = model.model_validate(data)
                self.assertNotIn("application_guide", old.model_dump()["benefit"])
                data["benefit"]["application_guide"] = None
                restored = model.model_validate(data)
                self.assertEqual(digest(old), digest(restored))
                self.assertNotIn("application_guide", restored.published(datetime.now(timezone.utc)).detail)

    def test_cash_and_service_exact_citations_and_whole_input_hash(self):
        for model, digest, data, original in self.cases():
            with self.subTest(model=model):
                data, text = with_guide(data, original)
                record = model.model_validate(data)
                check = validate_source_text if model is CatalogRecord else validate_service_sources
                check(record, text)
                public = record.published(datetime.now(timezone.utc)).detail
                self.assertEqual(public["application_guide"]["method"]["quote"], METHOD)
                changed = deepcopy(data)
                changed["benefit"]["application_guide"]["documents"] = None
                self.assertNotEqual(digest(record), digest(model.model_validate(changed)))
                changed["benefit"]["application_guide"]["documents"] = []
                self.assertNotEqual(digest(record), digest(model.model_validate(changed)))

    def test_wrong_versions_empty_citations_missing_keys_and_large_lists_rejected(self):
        mutations = (
            lambda g: g["method"]["source"].update(document_version="other"),
            lambda g: g["office"]["source"].update(checked_at="2026-11-02T00:00:00+00:00"),
            lambda g: g["documents"][0]["source"].update(url="https://different.example/notice"),
            lambda g: g["method"].update(quote="   "),
            lambda g: g["office"].update(location="\n"),
            lambda g: g["method"].pop("location"),
            lambda g: g.pop("documents"),
            lambda g: g.update(documents=[g["documents"][0]] * 101),
        )
        for model, _, data, original in self.cases():
            for mutate in mutations:
                item, _ = with_guide(data, original)
                mutate(item["benefit"]["application_guide"])
                with self.subTest(model=model, mutation=mutate), self.assertRaises(ValidationError):
                    model.model_validate(item)

    def test_quote_missing_from_exact_source_fails_cash_and_service_validation(self):
        for model, _, data, original in self.cases():
            item, text = with_guide(data, original)
            item["benefit"]["application_guide"]["method"]["quote"] = "지어낸 신청 방법"
            check = validate_source_text if model is CatalogRecord else validate_service_sources
            with self.subTest(model=model), self.assertRaisesRegex(ValueError, "quote_missing_from_source"):
                check(model.model_validate(item), text)

    def test_changed_guide_requires_new_approval_before_db_access(self):
        data, text = with_guide(release_data(), source_text())
        record = CatalogRecord.model_validate(data)
        approved = record_hash(record)
        data["benefit"]["application_guide"]["documents"] = None
        with patch("benefitfinder.infrastructure.database.repositories.published_policies.psycopg.connect") as connect:
            with self.assertRaisesRegex(ValueError, "invalid_catalog_approval"):
                publish_policy("unused", CatalogRecord.model_validate(data), source_text=text,
                               approved_sha256=approved, reviewer="fictional-reviewer",
                               reviewed_at=datetime.now(timezone.utc))
            connect.assert_not_called()

    def test_unknown_and_confirmed_absence_are_distinct_in_public_contract(self):
        for documents in (None, []):
            data = release_data()
            data["benefit"]["application_guide"] = {"method": None, "office": None, "documents": documents}
            public = CatalogRecord.model_validate(data).published(datetime.now(timezone.utc)).detail
            validator("benefit.schema.json").validate(public)
            self.assertEqual(public["application_guide"]["documents"], documents)

    def test_cash_and_service_http_detail_preserve_instructions(self):
        cash, _ = with_guide(release_data(), source_text())
        service, _ = with_guide(service_release_data(price=False), service_source_text())
        with TestClient(create_app(policy_repository=MemoryPolicies(cash),
                                  service_repository=MemoryServices(service))) as client:
            for data in (cash, service):
                response = client.get("/api/v1/benefits/" + data["benefit"]["id"])
                self.assertEqual(response.status_code, 200, response.text)
                validator("benefit.schema.json").validate(response.json())
                self.assertEqual(response.json()["application_guide"], data["benefit"]["application_guide"])
