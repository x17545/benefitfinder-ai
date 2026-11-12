"""F07 instructions through actual JSONB publication, approval checks and HTTP."""

from datetime import datetime, timezone
import json
from pathlib import Path
import unittest

from fastapi.testclient import TestClient
import psycopg
from psycopg.types.json import Jsonb

from backend.integration.postgres_fixture import test_database_url
from backend.integration.test_policy_source_rechecks_postgres import dedicated_database
from backend.tests.application_guide_fixtures import with_guide
from backend.tests.catalog_fixtures import release_data, source_text
from backend.tests.service_catalog_fixtures import service_release_data, service_source_text
from backend.tests.test_published_catalog import validator
from benefitfinder.bootstrap import create_app
from benefitfinder.infrastructure.database.repositories.published_policies import (
    PostgresPublishedPolicies, publish_policy,
)
from benefitfinder.infrastructure.database.repositories.published_services import (
    PostgresPublishedServices, publish_service,
)
from benefitfinder.infrastructure.policy_catalog_records import CatalogRecord, record_hash
from benefitfinder.infrastructure.service_catalog_records import ServiceCatalogRecord, service_record_hash

ROOT = Path(__file__).resolve().parents[2]


class ApplicationGuidePostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database = dedicated_database(test_database_url())
        cls.url = cls.database.__enter__()
        with psycopg.connect(cls.url) as connection:
            for name in ("0007_f12_analysis_tasks.sql", "0008_service_catalog.sql", "0009_gov24_policy_sources.sql"):
                connection.execute((ROOT / "backend/migrations/versions" / name).read_text())

    @classmethod
    def tearDownClass(cls) -> None:
        cls.database.__exit__(None, None, None)

    def setUp(self) -> None:
        with psycopg.connect(self.url) as connection:
            connection.execute("TRUNCATE public.policy_catalog_releases CASCADE")

    def publish(self) -> tuple[dict, dict]:
        cash, cash_text = with_guide(release_data(), source_text())
        service, service_text = with_guide(service_release_data(price=False), service_source_text())
        for model, digest, publish, data, text in (
            (CatalogRecord, record_hash, publish_policy, cash, cash_text),
            (ServiceCatalogRecord, service_record_hash, publish_service, service, service_text),
        ):
            item = model.model_validate(data)
            options = {"price_source_text": None} if model is ServiceCatalogRecord else {}
            publish(self.url, item, source_text=text, approved_sha256=digest(item),
                    reviewer="fictional-guide-reviewer", reviewed_at=datetime.now(timezone.utc), **options)
        return cash, service

    def test_publish_read_and_http_preserve_guides_without_changing_cash_or_service_values(self) -> None:
        cash, service = self.publish()
        with TestClient(create_app(policy_database_url=self.url)) as client:
            for data in (cash, service):
                detail = client.get("/api/v1/benefits/" + data["benefit"]["id"])
                self.assertEqual(detail.status_code, 200, detail.text)
                validator("benefit.schema.json").validate(detail.json())
                self.assertEqual(detail.json()["application_guide"], data["benefit"]["application_guide"])
            request = json.loads((ROOT / "contracts/fixtures/recommendation-request.json").read_text())
            request["profile"]["age"] = 25
            request["profile"]["domain_data"]["interests"] = ["employment", "housing"]
            response = client.post("/api/v1/recommendations", json=request)
            self.assertEqual(response.status_code, 200, response.text)
            validator("recommendation-response.schema.json").validate(response.json())
            self.assertEqual(response.json()["results"]["1y"]["pv_off"]["fallback_list"][0]["amount_in_period_won"], 1_000_000)
            self.assertIsNone(response.json()["services_total_won"])

    def test_direct_changes_to_published_instructions_are_rejected_for_both_kinds(self) -> None:
        cash, service = self.publish()
        for data, repository in (
            (cash, PostgresPublishedPolicies(self.url)),
            (service, PostgresPublishedServices(self.url)),
        ):
            data["benefit"]["application_guide"]["documents"] = None
            with self.assertRaisesRegex(psycopg.errors.RaiseException, "catalog_release_is_immutable"):
                with psycopg.connect(self.url) as connection:
                    connection.execute("UPDATE public.policy_catalog_releases SET payload=%s WHERE policy_id=%s",
                                       (Jsonb(data), data["benefit"]["id"]))
            retained = repository.get_published(data["benefit"]["id"])
            self.assertEqual(len(retained.detail["application_guide"]["documents"]), 2)
