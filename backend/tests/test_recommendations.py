
"""추천 API 응답 계약 자동 테스트."""

import copy
import json
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from benefitfinder.main import app


ROOT = Path(__file__).resolve().parents[2]


class RecommendationApiTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

        cls.payload = json.loads(
            (ROOT / "contracts/fixtures/recommendation-request.json")
            .read_text(encoding="utf-8")
        )

        schema_dir = ROOT / "contracts/v1"

        response_schema = json.loads(
            (schema_dir / "recommendation-response.schema.json")
            .read_text(encoding="utf-8")
        )

        common_schema = json.loads(
            (schema_dir / "common.schema.json")
            .read_text(encoding="utf-8")
        )

        registry = Registry().with_resource(
            "https://benefitfinder.example/contracts/v1/common.schema.json",
            Resource.from_contents(common_schema),
        )

        cls.validator = Draft202012Validator(
            response_schema,
            registry=registry,
            format_checker=FormatChecker(),
        )

    def test_recommendation_response_matches_contract(self):
        response = self.client.post(
            "/api/v1/recommendations",
            json=self.payload,
        )

        self.assertEqual(response.status_code, 200, response.text)

        data = response.json()
        errors = list(self.validator.iter_errors(data))

        self.assertEqual(
            errors,
            [],
            "\n".join(error.message for error in errors),
        )

        self.assertEqual(data["domain"], "youth")
        self.assertFalse(data["ai_generated"])
        self.assertEqual(data["discount_rate_status"], "unknown")
        self.assertEqual(
            set(data["results"]),
            {"1y", "2y", "all"},
        )

        first = data["results"]["1y"]["pv_off"]["portfolios"][0]
        self.assertEqual(first["total_won"], 5_400_000)

    def test_unsupported_domain_is_rejected(self):
        payload = copy.deepcopy(self.payload)
        payload["domain"] = "unknown"

        response = self.client.post(
            "/api/v1/recommendations",
            json=payload,
        )

        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
