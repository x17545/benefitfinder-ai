"""F13 source and calculation guards using virtual published clauses and fake completions."""

import json
from copy import deepcopy
from dataclasses import replace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from benefitfinder.ai.policy_explanation import GroundedPolicyExplainer
from benefitfinder.application.policy_explanations import (
    explain_policy,
    explanation_lead,
    safe_condition_results,
)
from benefitfinder.infrastructure.published_clauses import InMemoryPublishedClauses
from benefitfinder.jobs.explanation_example import explanation_example


class GroundedExplanationTests(IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.request, self.clauses = explanation_example("employment")
        self.repo = InMemoryPublishedClauses(self.clauses)
        self.good = {
            "details": [
                {
                    "text": "온라인 신청서와 구직활동 계획서를 준비해요.",
                    "clause_id": "virtual-application",
                    "quote": self.clauses[1].text,
                }
            ]
        }

    async def _explain(self, output: object):
        completion = AsyncMock()
        completion.complete.return_value = output
        result = await explain_policy(self.request, self.repo, GroundedPolicyExplainer(completion))
        return result, completion

    async def test_valid_detail_is_cited_and_code_owns_name_amount_and_verdict(self) -> None:
        result, _ = await self._explain(self.good)
        self.assertTrue(result.ai_generated)
        self.assertEqual(result.ai_sources, ("virtual-application",))
        self.assertEqual(result.evidence, (self.clauses[1],))
        self.assertTrue(result.ai_reason.startswith(explanation_lead(self.request)))
        self.assertIn("3,000,000원", result.ai_reason)
        self.assertIn("확인이 필요한 일반 조건", result.ai_reason)
        self.assertIn("가상 신청 안내", result.ai_reason)

    async def test_unsupported_numbers_sources_or_quotes_use_template(self) -> None:
        edits = (
            ("text", "요."),
            ("text", "월 금액은 999원이에요."),
            ("clause_id", "virtual-draft"),
            ("clause_id", "unretrieved-other-policy"),
            ("quote", "원문에 없는 가상 신청 문구"),
        )
        for field, value in edits:
            with self.subTest(field=field, value=value):
                output = deepcopy(self.good)
                output["details"][0][field] = value
                result, _ = await self._explain(output)
                self.assertFalse(result.ai_generated)
                self.assertEqual(result.ai_sources, ())
                self.assertTrue(result.ai_reason.startswith(explanation_lead(self.request)))
        result, _ = await self._explain(self.good | {"policy_name": "다른 가상 정책"})
        self.assertFalse(result.ai_generated)

    async def test_prompt_only_contains_safe_verdicts_and_retrieved_published_clauses(self) -> None:
        private = "F13_PRIVATE_MARKER"
        self.request = replace(
            self.request,
            conditions=safe_condition_results(
                {
                    "age": "met",
                    "income_band": private,
                    "parents": private,
                    "special_conditions": private,
                    "reason": private,
                }
            ),
        )
        result, completion = await self._explain(self.good)
        self.assertTrue(result.ai_generated)
        messages = completion.complete.call_args.args[0]
        user = json.loads(messages[1]["content"])
        self.assertEqual(user["condition_results"], {"age": "met"})
        self.assertNotIn(private, json.dumps(messages))
        self.assertNotIn("virtual-draft", json.dumps(user))
        self.assertNotIn("source_url", json.dumps(user))

    async def test_retrieval_filters_drafts_other_versions_and_other_policies(self) -> None:
        repo = InMemoryPublishedClauses(
            self.clauses
            + (
                replace(self.clauses[0], id="old", document_version="other"),
                replace(self.clauses[0], id="other", policy_id="youth:virtual:other"),
            )
        )
        found = await repo.search(self.request, limit=5)
        self.assertEqual({c.id for c in found}, {"virtual-eligibility", "virtual-application"})
