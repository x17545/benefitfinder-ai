"""Maturity support accrues before payment, but only real cash is discounted."""

from __future__ import annotations

import json
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.tests.catalog_fixtures import MemoryPolicies, release_data
from backend.tests.maturity_fixtures import maturity_release
from backend.tests.test_published_catalog import validator
from benefitfinder.application.published_recommendations import (
    PublishedRecommendations, map_published_response,
)
from benefitfinder.bootstrap import create_app
from benefitfinder.domain.present_value import (
    calculate_present_value, calculate_support_value, maturity_breakdown,
)
from benefitfinder.infrastructure.policy_catalog_records import (
    CatalogRecord, record_hash, validate_source_text,
)

ROOT = Path(__file__).resolve().parents[2]


class MaturityRecordTest(unittest.TestCase):
    def test_exact_source_approval_and_legacy_serialization(self):
        data, text = maturity_release()
        record = CatalogRecord.model_validate(data)
        validate_source_text(record, text)
        restored = CatalogRecord.model_validate_json(record.model_dump_json())
        self.assertEqual(record_hash(record), record_hash(restored))
        altered = deepcopy(data)
        altered["maturity_accruals"][0]["amount_won"] += 1
        altered["maturity_accruals"][1]["amount_won"] -= 1
        self.assertNotEqual(record_hash(record), record_hash(CatalogRecord.model_validate(altered)))
        legacy = CatalogRecord.model_validate(release_data()).model_dump(mode="json")
        self.assertNotIn("maturity_accruals", legacy)
        self.assertNotIn("maturity_accrual_evidence", legacy)

    def test_accrual_quote_must_match_exact_source_and_version(self):
        data, text = maturity_release()
        data["maturity_accrual_evidence"] = deepcopy(data["maturity_accrual_evidence"])
        data["maturity_accrual_evidence"][0]["quote"] = "[가상] 원문에 없는 적립 근거"
        with self.assertRaisesRegex(ValueError, "quote_missing_from_source"):
            validate_source_text(CatalogRecord.model_validate(data), text)
        data["maturity_accrual_evidence"][0]["source"]["document_version"] = "other"
        with self.assertRaisesRegex(ValueError, "evidence_source_mismatch"):
            CatalogRecord.model_validate(data)

    def test_incomplete_or_inconsistent_schedules_cannot_be_published(self):
        mutations = [
            lambda d: d.pop("maturity_accruals"),
            lambda d: d.pop("maturity_accrual_evidence"),
            lambda d: d.update(maturity_accruals=[]),
            lambda d: d.update(maturity_accrual_evidence=[]),
            lambda d: d["maturity_accruals"][0].update(month=0),
            lambda d: d["maturity_accruals"][0].update(month=12),
            lambda d: d["maturity_accruals"][0].update(month=37),
            lambda d: d["maturity_accruals"][0].update(amount_won=-1),
            lambda d: d["maturity_accruals"][0].update(amount_won=True),
            lambda d: d["maturity_accruals"][0].update(amount_won=99_999),
            lambda d: d["payments"][0].update(month=35),
            lambda d: d["benefit"]["benefit_amount"].update(type="lump_sum"),
            lambda d: d["benefit"]["benefit_amount"].update(pay_timing="once"),
            lambda d: d["benefit"]["benefit_amount"].update(period_months=None),
            lambda d: d["benefit"]["benefit_amount"].update(amount_won=10),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate), self.assertRaises(ValidationError):
                data, _ = maturity_release()
                mutate(data)
                CatalogRecord.model_validate(data)
        data, _ = maturity_release()
        data.pop("maturity_accruals")
        data.pop("maturity_accrual_evidence")
        with self.assertRaisesRegex(ValueError, "maturity_schedule_requires_reviewed_accruals"):
            CatalogRecord.model_validate(data)


class MaturityCalculationTest(unittest.TestCase):
    def setUp(self):
        self.data, _ = maturity_release()
        self.repository = MemoryPolicies(self.data, release_data("B", amount=400_000))
        self.service = PublishedRecommendations(self.repository, lambda profile, rule: "met")
        self.request = SimpleNamespace(
            domain="youth", profile=SimpleNamespace(domain_data=SimpleNamespace(interests=None)),
        )

    def test_uneven_accrual_is_not_a_prorated_or_discounted_cash_flow(self):
        policy = self.repository.policies[0]
        payments, accruals = list(policy.payments), list(policy.maturity_accruals)
        for period, accrued in [("1y", 300_000), ("2y", 600_000), ("all", 1_000_000)]:
            with self.subTest(period=period):
                self.assertEqual(calculate_support_value(
                    payments, period, maturity_accruals=accruals,
                ), accrued)
                cash = 1_000_000 if period == "all" else 0
                self.assertEqual(maturity_breakdown(payments, accruals, period), {
                    "cash_in_period_won": cash, "expected_accrual_won": accrued - cash,
                })
                self.assertEqual(calculate_support_value(
                    payments, period, 0, maturity_accruals=accruals,
                ), cash)
        self.assertEqual(calculate_support_value(
            payments, "all", 0.1, maturity_accruals=accruals,
        ), 751_315)

    def test_maturity_month_on_or_after_cutoff_and_rounding(self):
        for month in (12, 13, 24, 25):
            payments = [(month, 1_000_001)]
            accruals = [(1, 400_000), (month, 600_001)]
            for period, cutoff in (("1y", 12), ("2y", 24)):
                with self.subTest(month=month, period=period):
                    due = month <= cutoff
                    self.assertEqual(calculate_support_value(
                        payments, period, 0.035, maturity_accruals=accruals,
                    ), calculate_present_value(payments, 0.035) if due else 0)
                    self.assertEqual(maturity_breakdown(payments, accruals, period), {
                        "cash_in_period_won": 1_000_001 if due else 0,
                        "expected_accrual_won": 0 if due else 400_000,
                    })

    def test_six_views_use_cash_pv_and_expose_nominal_breakdowns(self):
        response = map_published_response(self.service(self.request, 0.1), 0.1)
        validator("recommendation-response.schema.json").validate(response)
        for period, expected in [("1y", 300_000), ("2y", 600_000), ("all", 0)]:
            for pv in ("pv_off", "pv_on"):
                with self.subTest(period=period, pv=pv):
                    portfolio = response["results"][period][pv]["portfolios"][0]
                    row = next(p for p in portfolio["policies"] if p["policy_id"].endswith(":M"))
                    self.assertEqual(row["amount_breakdown"]["expected_accrual_won"], expected)
                    self.assertEqual(portfolio["amount_breakdown"], {
                        "cash_in_period_won": 1_400_000 if period == "all" else 400_000,
                        "expected_accrual_won": expected,
                    })
                    self.assertEqual(portfolio["total_won"], sum(
                        p["amount_in_period_won"] for p in portfolio["policies"]
                    ))
                    if pv == "pv_on" and period != "all":
                        self.assertEqual(row["amount_in_period_won"], 0)

    def test_period_and_cash_pv_change_exact_portfolio_ranking(self):
        data = deepcopy(self.data)
        b = release_data("B", amount=400_000)
        b["benefit"]["conflicts"] = [{
            "policy_id": data["benefit"]["id"], "evidence": b["payment_evidence"],
        }]
        service = PublishedRecommendations(
            MemoryPolicies(data, b, release_data("C", amount=100_000)), lambda profile, rule: "met",
        )
        result = service(self.request, 0.1)["results"]
        for period, pv, member in (("1y", "pv_off", "B"), ("2y", "pv_off", "M"),
                                   ("all", "pv_on", "M"), ("2y", "pv_on", "B")):
            self.assertIn("youth:catalog:" + member, result[period][pv]["portfolios"][0]["members"])
        mapped = map_published_response(service(self.request, 0.1), 0.1)
        given_up = mapped["results"]["1y"]["pv_off"]["portfolios"][0]["given_up"][0]
        self.assertEqual(given_up["amount_breakdown"]["expected_accrual_won"], 300_000)

    def test_single_policy_fallback_and_unknown_rate(self):
        service = PublishedRecommendations(MemoryPolicies(self.data), lambda profile, rule: "met")
        response = map_published_response(service(self.request), None)
        validator("recommendation-response.schema.json").validate(response)
        for period in ("1y", "2y", "all"):
            self.assertEqual(response["results"][period]["pv_on"]["calculation_status"], "unavailable")
        row = response["results"]["1y"]["pv_off"]["fallback_list"][0]
        self.assertEqual(row["amount_in_period_won"], 300_000)
        self.assertEqual(row["amount_breakdown"]["cash_in_period_won"], 0)

    def test_simulation_and_lost_policy_preserve_expected_support_basis(self):
        # Age 34 -> 35 loses M; B remains, so before and after both have a portfolio.
        data = deepcopy(self.data)
        others = [release_data("B", amount=400_000), release_data("C", amount=100_000)]
        for other in others:
            other["conditions"][0]["maximum"] = 60
        repository = MemoryPolicies(data, *others)
        payload = json.loads((ROOT / "contracts/fixtures/simulation-request.json").read_text())
        payload.update(scenario="age_plus_one")
        payload.pop("income_change")
        payload["profile"]["age"] = 34
        payload["profile"]["domain_data"]["interests"] = ["housing"]
        with TestClient(create_app(policy_repository=repository)) as client:
            response = client.post("/api/v1/simulations", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        validator("simulation-response.schema.json").validate(result)
        self.assertEqual(repository.reads, 1)
        self.assertEqual(result["before_total_won"], 800_000)
        self.assertEqual(result["after_total_won"], 500_000)
        self.assertEqual(result["before_amount_breakdown"], {
            "cash_in_period_won": 500_000, "expected_accrual_won": 300_000,
        })
        self.assertNotIn("after_amount_breakdown", result)
        self.assertEqual(result["annual"]["before_amount_breakdown"], result["before_amount_breakdown"])
        self.assertEqual(result["lost"][0]["amount_breakdown"]["expected_accrual_won"], 300_000)
        self.assertIn("만기", result["calculation_note"])

    def test_zero_support_is_verified_and_legacy_responses_omit_metadata(self):
        data, _ = maturity_release()
        data["benefit"]["benefit_amount"].update(amount_won=0, max_total_won=0)
        data["payments"][0]["amount_won"] = 0
        for accrual in data["maturity_accruals"]:
            accrual["amount_won"] = 0
        # Structural zero is representable; source truth still requires human review.
        record = CatalogRecord.model_validate(data)
        self.assertEqual(sum(p.amount_won for p in record.maturity_accruals), 0)
        response = map_published_response(PublishedRecommendations(
            MemoryPolicies(release_data("A"), release_data("B")), lambda profile, rule: "met",
        )(self.request, 0.1), 0.1)
        self.assertNotIn("amount_breakdown", response["results"]["1y"]["pv_off"]["portfolios"][0])

    def test_simulation_pv_excludes_unpaid_accrual_but_annual_stays_nominal(self):
        other = [release_data("B", amount=400_000), release_data("C", amount=100_000)]
        for data in other:
            data["conditions"][0]["maximum"] = 60
        app = create_app(policy_repository=MemoryPolicies(self.data, *other))
        app.state.discount_rate = 0.1
        payload = json.loads((ROOT / "contracts/fixtures/simulation-request.json").read_text())
        payload.update(scenario="age_plus_one", options={"period": "1y", "present_value": True})
        payload.pop("income_change")
        payload["profile"]["age"] = 34
        payload["profile"]["domain_data"]["interests"] = ["housing"]
        with TestClient(app) as client:
            response = client.post("/api/v1/simulations", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        validator("simulation-response.schema.json").validate(result)
        cash_pv = sum(calculate_present_value([(1, amount)], 0.1) for amount in (400_000, 100_000))
        self.assertEqual(result["before_total_won"], cash_pv)
        self.assertEqual(result["after_total_won"], cash_pv)
        self.assertEqual(result["annual"]["before_won"] - result["annual"]["after_won"], 300_000)
        self.assertEqual(result["lost"][0]["amount_won"], 0)
        self.assertEqual(result["lost"][0]["amount_breakdown"]["expected_accrual_won"], 300_000)


if __name__ == "__main__":
    unittest.main()
