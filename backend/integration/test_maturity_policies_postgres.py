"""Fictional maturity-policy publication, approval binding and HTTP regressions."""

from __future__ import annotations

import json
import unittest
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from backend.integration.postgres_fixture import migrate_test_database, test_database_url
from backend.tests.catalog_fixtures import release_data, source_text
from backend.tests.maturity_fixtures import maturity_release
from backend.tests.test_published_catalog import validator
from benefitfinder.application.base_rates import BaseRateObservation
from benefitfinder.application.published_policies import PolicyCatalogUnavailable
from benefitfinder.bootstrap import create_app
from benefitfinder.infrastructure.database.repositories.published_policies import (
    PostgresPublishedPolicies,
    publish_policy,
)
from benefitfinder.infrastructure.policy_catalog_records import CatalogRecord, record_hash

ROOT = Path(__file__).resolve().parents[2]
REVIEWED_AT = datetime(2026, 11, 2, tzinfo=timezone.utc)
MATURITY_ID = "youth:catalog:M"
ACCRUAL_SCHEDULE = ((1, 100_000), (12, 200_000), (24, 300_000), (36, 400_000))


class FixedRates:
    def latest(self) -> BaseRateObservation:
        return BaseRateObservation(
            annual_rate=0.03,
            observed_on=date(2026, 11, 2),
            checked_at=datetime(2026, 11, 3, tzinfo=timezone.utc),
        )


class MaturityPoliciesPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.url = test_database_url()
        migrate_test_database(cls.url)

    def setUp(self) -> None:
        with psycopg.connect(self.url) as connection:
            connection.execute("TRUNCATE public.policy_catalog_releases")
        self.repository = PostgresPublishedPolicies(self.url)

    def publish(self, data: dict[str, Any], text: str) -> tuple[CatalogRecord, str]:
        record = CatalogRecord.model_validate(data)
        digest = record_hash(record)
        publish_policy(
            self.url,
            record,
            source_text=text,
            approved_sha256=digest,
            reviewer="fictional-maturity-reviewer",
            reviewed_at=REVIEWED_AT,
        )
        return record, digest

    def request_payload(self) -> dict[str, Any]:
        payload = json.loads(
            (ROOT / "contracts/fixtures/recommendation-request.json").read_text()
        )
        payload["profile"]["age"] = 25
        payload["profile"]["domain_data"]["interests"] = ["housing"]
        return payload

    def test_publish_roundtrip_recommendation_given_up_and_simulation(self) -> None:
        maturity, maturity_text = maturity_release("M")
        given_up, given_up_text = maturity_release("C")
        cash = release_data("B", amount=2_000_000)
        cash["benefit"]["conflicts"] = [
            {
                "policy_id": given_up["benefit"]["id"],
                "evidence": cash["payment_evidence"],
            }
        ]

        legacy_payload = CatalogRecord.model_validate(cash).model_dump(mode="json")
        self.assertNotIn("maturity_accruals", legacy_payload)
        self.assertNotIn("maturity_accrual_evidence", legacy_payload)

        self.publish(maturity, maturity_text)
        self.publish(cash, source_text(2_000_000))
        self.publish(given_up, given_up_text)

        with psycopg.connect(self.url) as connection:
            stored = connection.execute(
                "SELECT payload FROM public.policy_catalog_releases WHERE policy_id=%s",
                (MATURITY_ID,),
            ).fetchone()[0]
        self.assertEqual(
            stored["maturity_accruals"],
            [{"month": month, "amount_won": amount} for month, amount in ACCRUAL_SCHEDULE],
        )
        self.assertEqual(stored["maturity_accrual_evidence"], maturity["maturity_accrual_evidence"])
        restored = CatalogRecord.model_validate(stored)
        self.assertEqual(
            tuple((item.month, item.amount_won) for item in restored.maturity_accruals or ()),
            ACCRUAL_SCHEDULE,
        )
        published = self.repository.get_published(MATURITY_ID)
        self.assertIsNotNone(published)
        self.assertEqual(published.maturity_accruals, ACCRUAL_SCHEDULE)

        request = self.request_payload()
        with TestClient(create_app(policy_database_url=self.url)) as client:
            response = client.post("/api/v1/recommendations", json=request)
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            validator("recommendation-response.schema.json").validate(body)
            self.assertIsNone(body["discount_rate"])
            self.assertEqual(body["discount_rate_status"], "unknown")
            self.assertEqual(body["view"], "portfolio")

            for period in ("1y", "2y", "all"):
                self.assertEqual(
                    body["results"][period]["pv_on"]["calculation_status"],
                    "unavailable",
                )
                self.assertEqual(
                    body["results"][period]["pv_on"]["unavailable_reason"],
                    "missing_discount_rate",
                )

            expected = {
                "1y": (2_300_000, 2_000_000, 300_000, 300_000, 0, 300_000),
                "2y": (2_600_000, 2_000_000, 600_000, 600_000, 0, 600_000),
                "all": (3_000_000, 3_000_000, 0, 1_000_000, 1_000_000, 0),
            }
            for period, values in expected.items():
                total, cash_won, accrual_won, policy_won, policy_cash, policy_accrual = values
                portfolio = body["results"][period]["pv_off"]["portfolios"][0]
                self.assertEqual(portfolio["total_won"], total)
                self.assertEqual(
                    portfolio["amount_breakdown"],
                    {
                        "cash_in_period_won": cash_won,
                        "expected_accrual_won": accrual_won,
                    },
                )
                policies = {item["policy_id"]: item for item in portfolio["policies"]}
                self.assertEqual(policies[MATURITY_ID]["amount_in_period_won"], policy_won)
                self.assertEqual(
                    policies[MATURITY_ID]["amount_breakdown"],
                    {
                        "cash_in_period_won": policy_cash,
                        "expected_accrual_won": policy_accrual,
                    },
                )
                self.assertEqual(policies[MATURITY_ID]["original_amount_won"], 1_000_000)
                self.assertNotIn("amount_breakdown", policies["youth:catalog:B"])

            given_up_item = body["results"]["1y"]["pv_off"]["portfolios"][0]["given_up"][0]
            self.assertEqual(given_up_item["policy_id"], "youth:catalog:C")
            self.assertEqual(given_up_item["amount_won"], 300_000)
            self.assertEqual(
                given_up_item["amount_breakdown"],
                {"cash_in_period_won": 0, "expected_accrual_won": 300_000},
            )

            detail = client.get("/api/v1/benefits/" + MATURITY_ID)
            self.assertEqual(detail.status_code, 200, detail.text)
            validator("benefit.schema.json").validate(detail.json())
            self.assertEqual(detail.json()["source"]["document_version"], "fictional-v1")

            simulation = client.post(
                "/api/v1/simulations",
                json={**request, "scenario": "age_plus_one"},
            )
            self.assertEqual(simulation.status_code, 200, simulation.text)
            simulated = simulation.json()
            validator("simulation-response.schema.json").validate(simulated)
            self.assertEqual(simulated["before_total_won"], 2_300_000)
            self.assertEqual(simulated["after_total_won"], 2_300_000)
            expected_breakdown = {
                "cash_in_period_won": 2_000_000,
                "expected_accrual_won": 300_000,
            }
            self.assertEqual(simulated["before_amount_breakdown"], expected_breakdown)
            self.assertEqual(simulated["after_amount_breakdown"], expected_breakdown)
            self.assertEqual(simulated["annual"]["before_amount_breakdown"], expected_breakdown)
            self.assertEqual(simulated["annual"]["after_amount_breakdown"], expected_breakdown)

    def test_maturity_fallback_uses_accrual_nominal_and_cash_only_present_value(self) -> None:
        maturity, text = maturity_release("M")
        self.publish(maturity, text)
        request = self.request_payload()

        with TestClient(create_app(policy_database_url=self.url)) as client:
            response = client.post("/api/v1/recommendations", json=request)
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            validator("recommendation-response.schema.json").validate(body)
            self.assertEqual(body["view"], "fallback_list")
            for period, amount, accrual in (
                ("1y", 300_000, 300_000),
                ("2y", 600_000, 600_000),
                ("all", 1_000_000, 0),
            ):
                item = body["results"][period]["pv_off"]["fallback_list"][0]
                self.assertEqual(item["amount_in_period_won"], amount)
                self.assertEqual(
                    item["amount_breakdown"],
                    {
                        "cash_in_period_won": amount - accrual,
                        "expected_accrual_won": accrual,
                    },
                )
                self.assertEqual(
                    body["results"][period]["pv_on"]["unavailable_reason"],
                    "missing_discount_rate",
                )

        with TestClient(
            create_app(policy_database_url=self.url, base_rate_repository=FixedRates())
        ) as client:
            response = client.post("/api/v1/recommendations", json=request)
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            validator("recommendation-response.schema.json").validate(body)
            self.assertEqual(body["discount_rate"], 0.03)
            self.assertEqual(body["discount_rate_status"], "verified")
            for period, amount in (("1y", 0), ("2y", 0), ("all", 915_142)):
                item = body["results"][period]["pv_on"]["fallback_list"][0]
                self.assertEqual(item["amount_in_period_won"], amount)

    def test_changed_accruals_evidence_or_source_require_a_new_approval(self) -> None:
        maturity, text = maturity_release("M")
        record, digest = self.publish(maturity, text)

        def swap_accruals(data: dict[str, Any]) -> None:
            first, second = data["maturity_accruals"][:2]
            first["amount_won"], second["amount_won"] = (
                second["amount_won"],
                first["amount_won"],
            )

        def change_accrual_evidence(data: dict[str, Any]) -> None:
            data["maturity_accrual_evidence"] = [
                {**item, "location": item["location"] + " 변경"}
                for item in data["maturity_accrual_evidence"]
            ]

        for label, mutate in (
            ("same-total schedule", swap_accruals),
            ("accrual evidence", change_accrual_evidence),
        ):
            altered_data = deepcopy(maturity)
            mutate(altered_data)
            altered = CatalogRecord.model_validate(altered_data)
            self.assertNotEqual(record_hash(altered), digest)
            with self.subTest(label=label), self.assertRaisesRegex(
                ValueError, "invalid_catalog_approval"
            ):
                publish_policy(
                    self.url,
                    altered,
                    source_text=text,
                    approved_sha256=digest,
                    reviewer="fictional-maturity-reviewer",
                    reviewed_at=REVIEWED_AT,
                )

        with self.assertRaisesRegex(ValueError, "source_hash_mismatch"):
            publish_policy(
                self.url,
                record,
                source_text=text + "\n[가상] 변조된 원문",
                approved_sha256=digest,
                reviewer="fictional-maturity-reviewer",
                reviewed_at=REVIEWED_AT,
            )

        retained = self.repository.list_published()
        self.assertEqual(len(retained), 1)
        self.assertEqual(retained[0].policy_id, MATURITY_ID)
        self.assertEqual(retained[0].maturity_accruals, ACCRUAL_SCHEDULE)

    def test_stored_accrual_payload_tampering_fails_closed_on_read(self) -> None:
        maturity, text = maturity_release("M")
        _, digest = self.publish(maturity, text)
        tampered = deepcopy(maturity)
        tampered["maturity_accruals"][0]["amount_won"] = 200_000
        tampered["maturity_accruals"][1]["amount_won"] = 100_000
        self.assertEqual(sum(item["amount_won"] for item in tampered["maturity_accruals"]), 1_000_000)

        with psycopg.connect(self.url) as connection:
            connection.execute(
                "UPDATE public.policy_catalog_releases SET payload=%s WHERE policy_id=%s",
                (Jsonb(CatalogRecord.model_validate(tampered).model_dump(mode="json")), MATURITY_ID),
            )
            approved = connection.execute(
                "SELECT approved_sha256 FROM public.policy_catalog_releases WHERE policy_id=%s",
                (MATURITY_ID,),
            ).fetchone()[0]
        self.assertEqual(approved, digest)

        with self.assertRaises(PolicyCatalogUnavailable):
            self.repository.list_published()
        with TestClient(create_app(policy_database_url=self.url)) as client:
            response = client.post("/api/v1/recommendations", json=self.request_payload())
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["code"], "dependency_unavailable")


if __name__ == "__main__":
    unittest.main()
