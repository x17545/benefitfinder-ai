"""F13 through the recommendation HTTP boundary, using only virtual input and fake completions."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest import TestCase
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from benefitfinder.ai.policy_explanation import GroundedPolicyExplainer
from benefitfinder.application.policy_explanations import (
    ExplanationRequest,
    GeneratedExplanation,
    PublishedClause,
)
from benefitfinder.bootstrap import create_app
from benefitfinder.infrastructure.example_explanation_data import recommendation_explanation_data
from benefitfinder.infrastructure.published_clauses import InMemoryPublishedClauses

ROOT = Path(__file__).resolve().parents[2]


def all_policies(response: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        policy
        for period in response["results"].values()
        for view in period.values()
        for portfolio in view["portfolios"]
        for policy in portfolio["policies"]
    ]


class RecommendationExplanationTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        schemas = {
            path.name: json.loads(path.read_text())
            for path in (ROOT / "contracts/v1").glob("*.schema.json")
        }
        registry = Registry().with_resources(
            (schema["$id"], Resource.from_contents(schema)) for schema in schemas.values()
        )
        cls.validator = Draft202012Validator(
            schemas["recommendation-response.schema.json"],
            registry=registry,
            format_checker=FormatChecker(),
        )

    def setUp(self) -> None:
        self.payload = json.loads(
            (ROOT / "contracts/fixtures/recommendation-request.json").read_text()
        )
        self.templates, self.clauses = recommendation_explanation_data()
        self.completion = AsyncMock()

        async def complete(
            messages: list[dict[str, str]], schema: dict[str, object], *, timeout_seconds: float
        ) -> object:
            payload = json.loads(messages[1]["content"])
            clause = next(c for c in payload["clauses"] if c["id"].endswith("application"))
            text = clause["text"].replace("제출합니다.", "제출해요.")
            return {"details": [{"text": text, "clause_id": clause["id"], "quote": clause["text"]}]}

        self.completion.complete.side_effect = complete

    def post(self, **kwargs: Any) -> dict[str, Any]:
        with TestClient(create_app(**kwargs)) as client:
            response = client.post("/api/v1/recommendations", json=self.payload)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.validator.validate(result)
        return result

    def assert_calculations_unchanged(
        self, baseline: dict[str, Any], generated: dict[str, Any]
    ) -> None:
        for period, views in baseline["results"].items():
            for pv_key, view in views.items():
                got = generated["results"][period][pv_key]
                self.assertEqual(got["calculation_status"], view["calculation_status"])
                for before, after in zip(view["portfolios"], got["portfolios"], strict=True):
                    self.assertEqual(before["total_won"], after["total_won"])
                    self.assertEqual(before["rank"], after["rank"])
                    for a, b in zip(before["policies"], after["policies"], strict=True):
                        for field in (
                            "policy_id",
                            "amount_in_period_won",
                            "original_amount_won",
                            "amount_status",
                            "eligibility",
                            "source",
                        ):
                            self.assertEqual(a[field], b[field])

    def test_generated_explanations_cover_each_period_without_repeated_model_calls(self) -> None:
        baseline = self.post()
        repo = AsyncMock(wraps=InMemoryPublishedClauses(self.clauses))
        app = create_app(
            explanation_repository=repo,
            explanation_generator=GroundedPolicyExplainer(self.completion),
        )
        with TestClient(app) as client:
            first = client.post("/api/v1/recommendations", json=self.payload).json()
            second = client.post("/api/v1/recommendations", json=self.payload).json()
        self.assert_calculations_unchanged(baseline, first)
        self.validator.validate(first)
        self.validator.validate(second)
        self.assertTrue(first["ai_generated"])
        self.assertTrue(second["ai_generated"])
        self.assertEqual(
            self.completion.complete.await_count, 6
        )  # 3 policies per request, not 6 views.
        self.assertEqual(repo.search.await_count, 6)
        for policy in all_policies(first):
            self.assertTrue(policy["ai_generated"])
            self.assertIn(f"{policy['amount_in_period_won']:,}원", policy["ai_reason"])
            self.assertIn("확인이 필요한 일반 조건", policy["ai_reason"])
            self.assertEqual(len(policy["ai_sources"]), 1)
            evidence = policy["ai_sources"][0]
            source = next(c for c in self.clauses if c.id == evidence["location"])
            self.assertEqual(evidence["quote"], source.text)
            self.assertEqual(evidence["source"]["document_version"], source.document_version)
            self.assertEqual(evidence["source"]["url"], source.source_url)

    def test_model_error_and_timeout_keep_templates_and_calculations(self) -> None:
        baseline = self.post()

        class SlowGenerator:
            async def generate(
                self,
                request: ExplanationRequest,
                clauses: tuple[PublishedClause, ...],
                *,
                timeout_seconds: float,
            ) -> GeneratedExplanation:
                await asyncio.sleep(1)
                raise RuntimeError("expected_timeout")

        failed = AsyncMock()
        failed.generate.side_effect = RuntimeError("F13_PRIVATE_FAILURE")
        for generator in (failed, SlowGenerator()):
            with self.subTest(generator=type(generator).__name__):
                start = time.monotonic()
                result = self.post(
                    explanation_generator=generator, explanation_timeout_seconds=0.03
                )
                self.assertLess(time.monotonic() - start, 0.8)
                self.assertFalse(result["ai_generated"])
                self.assert_calculations_unchanged(baseline, result)
                for policy in all_policies(result):
                    self.assertFalse(policy["ai_generated"])
                    self.assertEqual(policy["ai_sources"], [])
                    self.assertIn("공식 공고에서 확인", policy["ai_reason"])
                self.assertNotIn("F13_PRIVATE_FAILURE", json.dumps(result))

    def test_partial_failure_has_per_policy_ai_flags(self) -> None:
        good = self.completion.complete.side_effect

        async def partial(
            messages: list[dict[str, str]], schema: dict[str, object], *, timeout_seconds: float
        ) -> object:
            if "새출발" in messages[1]["content"]:
                raise OSError("F13_PRIVATE_FAILURE")
            return await good(messages, schema, timeout_seconds=timeout_seconds)

        self.completion.complete.side_effect = partial
        result = self.post(explanation_generator=GroundedPolicyExplainer(self.completion))
        self.assertTrue(result["ai_generated"])
        for row in all_policies(result):
            self.assertEqual(row["ai_generated"], not row["policy_id"].endswith("P3"))
            self.assertEqual(bool(row["ai_sources"]), row["ai_generated"])

    def test_private_profile_fields_and_verdicts_never_reach_completion(self) -> None:
        self.payload["profile"]["income_band"] = "between_15m_30m"
        data = self.payload["profile"]["domain_data"]
        data["asset_band"] = "between_122m_345m"
        data["special"] = ["disabled"]
        data["household"]["parent_income_band"] = "between_3m_5m"
        data["household"]["parent_asset_band"] = "between_300m_500m"
        result = self.post(explanation_generator=GroundedPolicyExplainer(self.completion))
        self.assertTrue(result["ai_generated"])
        for call in self.completion.complete.call_args_list:
            user = json.loads(call.args[0][1]["content"])
            self.assertLessEqual(set(user["condition_results"]), {"age", "employment"})
            serialized = json.dumps(user)
            for private in (
                "income",
                "asset",
                "special",
                "parent",
                "disabled",
                "between_15m_30m",
                "between_122m_345m",
                "between_3m_5m",
                "between_300m_500m",
            ):
                self.assertNotIn(private, serialized)

    def test_missing_draft_and_old_version_evidence_do_not_call_model(self) -> None:
        for clauses in (
            (),
            tuple(replace(c, published=False) for c in self.clauses),
            tuple(replace(c, document_version="old-version") for c in self.clauses),
        ):
            self.completion.reset_mock()
            result = self.post(
                explanation_repository=InMemoryPublishedClauses(clauses),
                explanation_generator=GroundedPolicyExplainer(self.completion),
            )
            self.assertFalse(result["ai_generated"])
            self.completion.complete.assert_not_called()
