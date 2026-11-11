"""The real FastAPI routes share the recommendation catalogue and calculation."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from benefitfinder.bootstrap import create_app

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
SIMULATIONS = "/api/v1/simulations"
BENEFITS = "/api/v1/benefits/"


class S4S5ApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(create_app())
        self.addCleanup(self.client.close)
        self.payload = json.loads((CONTRACTS / "fixtures/simulation-request.json").read_text())
        self.registry = Registry().with_resources(
            (document["$id"], Resource.from_contents(document))
            for path in (CONTRACTS / "v1").glob("*.schema.json")
            for document in [json.loads(path.read_text())]
        )

    def assert_contract(self, schema: str, body: dict[str, Any]) -> None:
        schema_doc = json.loads((CONTRACTS / "v1" / f"{schema}.schema.json").read_text())
        Draft202012Validator(
            schema_doc, registry=self.registry, format_checker=FormatChecker(),
        ).validate(body)

    def simulate(self) -> dict[str, Any]:
        self.assert_contract("simulation-request", self.payload)
        response = self.client.post(SIMULATIONS, json=self.payload)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assert_contract("simulation-response", result)
        self.assertIs(result["virtual_data"], True)
        self.assertIsNone(result["todo"])
        return result

    def recommend(self) -> dict[str, Any]:
        response = self.client.post(
            "/api/v1/recommendations",
            json={key: self.payload[key] for key in ("domain", "profile", "options")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["results"]

    def test_housing_income_cliff_keeps_period_total_and_nominal_annual_separate(self) -> None:
        self.payload["options"] = {"period": "2y", "present_value": True}
        expected = self.recommend()["2y"]["pv_on"]["portfolios"][0]["total_won"]
        result = self.simulate()
        self.assertEqual(result["before_total_won"], expected)
        self.assertEqual(result["basis_period"], "2y")
        self.assertEqual(result["annual"], {"before_won": 2_400_000, "after_won": 1_500_000})
        self.assertEqual(result["effective_marginal_rate"], 45)
        # P6 already wins on 2y, so it is not labelled as newly eligible.
        self.assertEqual(result["before_total_won"], result["after_total_won"])
        self.assertEqual((result["lost"], result["gained"]), ([], []))
        self.assertIn("5,000", result["next_threshold"]["text"])
        self.assertIn("장기 주거 정착 지원", result["next_threshold"]["text"])
        self.payload["options"] = {"period": "1y", "present_value": False}
        nominal = self.simulate()
        self.assertEqual(nominal["lost"][0]["policy_id"], "youth:example:P1")
        self.assertEqual(nominal["gained"][0]["policy_id"], "youth:example:P6")
        self.assertIn("새 자격", nominal["gained"][0]["reason"])

    def test_employment_then_details_share_source_with_recommendation(self) -> None:
        self.payload.pop("income_change")
        self.payload["scenario"] = "employed"
        self.payload["profile"].update(employment_status="job_seeker", income_band="none")
        top = self.recommend()["1y"]["pv_off"]["portfolios"][0]
        result = self.simulate()
        self.assertEqual(result["before_total_won"], top["total_won"])
        self.assertEqual(result["annual"], {"before_won": 5_400_000, "after_won": 2_400_000})
        self.assertEqual(result["effective_marginal_rate"], 12.5)
        self.assertEqual(result["lost"][0]["policy_id"], "youth:example:P3")
        self.assertEqual(result["income_change"]["to_won"], 24_000_000)

        for policy in top["policies"]:
            with self.subTest(policy_id=policy["policy_id"]):
                response = self.client.get(BENEFITS + quote(policy["policy_id"], safe=""))
                self.assertEqual(response.status_code, 200)
                detail = response.json()
                self.assert_contract("benefit", detail)
                self.assertEqual(detail["id"], policy["policy_id"])
                self.assertEqual(detail["source"], policy["source"])
                self.assertTrue(detail["virtual_data"])
                self.assertIsNone(detail["application"]["url"])
                self.assertTrue(detail["eligibility_conditions"])
                self.assertNotIn("profile", detail)
                self.assertNotIn("eligibility", detail)
        self.assertEqual(self.recommend()["1y"]["pv_off"]["portfolios"][0], top)

    def test_independence_and_age_change_leave_other_conditions_intact(self) -> None:
        self.payload.pop("income_change")
        self.payload["scenario"] = "independent"
        data = self.payload["profile"]["domain_data"]
        data["housing"]["living"] = "with_parents"
        data["contract"]["type"] = "none"
        result = self.simulate()
        self.assertEqual((result["before_total_won"], result["after_total_won"]), (1_500_000, 2_400_000))
        self.assertIsNone(result["income_change"])
        self.assertIn("월세", result["calculation_note"])

        data["housing"]["living"] = "alone"
        self.assertEqual(self.simulate()["gained"], [])
        self.payload["scenario"] = "age_plus_one"
        self.payload["profile"].update(age=34, employment_status="job_seeker")
        data["military_months_range"] = {"min": 0, "max": 0}
        self.assertEqual(self.simulate()["lost"][0]["policy_id"], "youth:example:P3")
        data["military_months_range"] = {"min": 0, "max": 24}
        self.assertEqual(self.simulate()["lost"], [])

    def test_unknown_income_and_zero_growth_do_not_invent_rate(self) -> None:
        self.payload.pop("income_change")
        self.payload["profile"]["income_band"] = "unknown"
        result = self.simulate()
        self.assertIsNone(result["income_change"])
        self.assertIsNone(result["effective_marginal_rate"])
        self.assertIn("대표값이 없어", result["calculation_note"])

        self.payload["profile"]["income_band"] = "between_15m_30m"
        self.payload["income_change"] = {"from_won": 30_000_000, "to_won": 30_000_000}
        result = self.simulate()
        self.assertIsNone(result["effective_marginal_rate"])
        self.assertEqual(result["annual"]["after_won"], 2_400_000)
        self.payload["income_change"]["to_won"] += 1
        self.assertEqual(self.simulate()["annual"]["after_won"], 1_500_000)

    def test_unknown_policy_and_invalid_requests_use_common_errors(self) -> None:
        self.payload["domain"] = "other"
        unsupported = self.client.post(SIMULATIONS, json=self.payload)
        self.payload["domain"] = "youth"
        self.payload["income_change"]["from_won"] = 1
        inconsistent = self.client.post(SIMULATIONS, json=self.payload)
        self.payload["scenario"] = "independent"
        bad_input = self.client.post(SIMULATIONS, json=self.payload)
        missing = self.client.get(BENEFITS + "youth%3Aexample%3Amissing")
        for response, status, code in (
            (unsupported, 400, "unsupported_domain"),
            (inconsistent, 422, "invalid_request"),
            (bad_input, 422, "invalid_request"),
            (missing, 404, "not_found"),
        ):
            self.assertEqual(response.status_code, status)
            self.assert_contract("error", response.json())
            self.assertEqual(response.json()["code"], code)
