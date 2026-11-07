"""F13 source and calculation guards using virtual published clauses and fake completions."""

import json
from copy import deepcopy
from dataclasses import replace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from benefitfinder.ai.policy_explanation import GroundedPolicyExplainer
from benefitfinder.application.policy_explanations import (
    ExplanationRequest,
    PublishedClause,
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


class GroundedExplanationReviewTests(IsolatedAsyncioTestCase):
    def _review_request_and_clauses(self) -> tuple[ExplanationRequest, tuple[PublishedClause, ...]]:
        request = ExplanationRequest(
            "youth:virtual:employment",
            "[가상] 새출발 취업준비 지원",
            "employment",
            "fictional-v1",
            None,
            (),
        )
        clauses = (
            PublishedClause(
                "virtual-review-exclusion",
                "youth:virtual:employment",
                "fictional-v1",
                "가상 지원 제외",
                "가상 공고의 지원 제외 조건과 예외 조항입니다.",
                "https://example.invalid/f13/employment",
                ("application",),
                False,
            ),
            PublishedClause(
                "virtual-review-report",
                "youth:virtual:employment",
                "fictional-v1",
                "가상 보고서 안내",
                "가상 공고의 구직활동 보고서 제출 조건 조항입니다.",
                "https://example.invalid/f13/employment",
                ("application",),
                False,
            ),
        )
        return request, clauses

    async def _generate(self, purpose: str, text: str, clause_index: int = 0):
        request, clauses = self._review_request_and_clauses()
        completion = AsyncMock()
        output = {
            "details": [
                {
                    "text": text,
                    "clause_id": clauses[clause_index].id,
                    "quote": clauses[clause_index].text,
                }
            ]
        }
        completion.complete.return_value = output
        explainer = GroundedPolicyExplainer(completion, purpose=purpose)  # type: ignore[arg-type]
        result = await explainer.generate(request, clauses, timeout_seconds=5.0)
        return result, completion, request, clauses

    async def test_invalid_purpose_rejected(self) -> None:
        completion = AsyncMock()
        with self.assertRaisesRegex(ValueError, "invalid_explanation_purpose"):
            GroundedPolicyExplainer(completion, purpose="preview")  # type: ignore[arg-type]

    async def test_application_default_prompt_preserved(self) -> None:
        request, clauses = self._review_request_and_clauses()
        for explainer in (
            GroundedPolicyExplainer(AsyncMock()),
            GroundedPolicyExplainer(AsyncMock(), purpose="application"),
        ):
            completion = explainer._completion  # type: ignore[attr-defined]
            completion.complete.return_value = {
                "details": [
                    {
                        "text": "지원 제외 조건과 예외를 공고에서 확인해요.",
                        "clause_id": clauses[0].id,
                        "quote": clauses[0].text,
                    }
                ]
            }
            result = await explainer.generate(request, clauses, timeout_seconds=5.0)
            self.assertTrue(result.text.startswith("지원 제외 조건과 예외를 공고에서 확인해요."))
            system = completion.complete.call_args.args[0][0]["content"]
            self.assertIn("게시된 조항", system)
            self.assertNotIn("사람이 승인한", system)

    async def test_review_prompt_requires_approval_scope_and_style(self) -> None:
        _, completion, _, _ = await self._generate(
            "review", "지원 제외 조건과 예외를 공고에서 확인해요."
        )
        system = completion.complete.call_args.args[0][0]["content"]
        for keyword in (
            "사람이 승인한",
            "한 문장",
            "자격",
            "지급",
            "모집",
            "숫자",
            "정책명",
            "기한",
            "quote",
            "clause_id",
            "해요체",
        ):
            with self.subTest(keyword=keyword):
                self.assertIn(keyword, system)

    async def test_review_accepts_check_sentences_without_draft_filter(self) -> None:
        for text, index in (
            ("지원 제외 조건과 예외를 공고에서 확인해요.", 0),
            ("구직활동 보고서 제출 조건을 확인해요.", 1),
        ):
            with self.subTest(text=text):
                result, _, _, clauses = await self._generate("review", text, index)
                self.assertTrue(result.text.startswith(text))
                self.assertEqual(result.source_ids, (clauses[index].id,))

    async def test_review_quote_choices_are_exact_source_lines(self) -> None:
        _, completion, _, clauses = await self._generate(
            "review", "지원 제외 조건과 예외를 공고에서 확인해요."
        )
        schema = completion.complete.call_args.args[1]
        options = schema["properties"]["details"]["items"]["properties"]["quote"]["enum"]
        self.assertTrue(options)
        self.assertTrue(all(any(quote in clause.text for clause in clauses) for quote in options))

    async def test_review_requires_a_check_action_instead_of_a_plain_assertion(self) -> None:
        with self.assertRaisesRegex(ValueError, "explanation_review_action_required"):
            await self._generate("review", "이 공고에는 참여 제한이 있어요.")

    async def test_review_rejects_guarantee_assertions_narrowly(self) -> None:
        for text in (
            "자격 충족이 인정돼서 혜택을 확인해요.",
            "지원금이 지급돼서 혜택을 확인해요.",
            "선정이 보장돼서 혜택을 확인해요.",
            "지원 대상이라 신청할 수 있어요.",
            "신청이 가능해요.",
        ):
            with self.subTest(text=text):
                request, clauses = self._review_request_and_clauses()
                completion = AsyncMock()
                completion.complete.return_value = {
                    "details": [
                        {
                            "text": text,
                            "clause_id": clauses[0].id,
                            "quote": clauses[0].text,
                        }
                    ]
                }
                explainer = GroundedPolicyExplainer(completion, purpose="review")
                with self.assertRaisesRegex(ValueError, "explanation_evidence_mismatch"):
                    await explainer.generate(request, clauses, timeout_seconds=5.0)

    async def test_review_reuses_evidence_checks_and_safe_payload(self) -> None:
        request, clauses = self._review_request_and_clauses()
        completion = AsyncMock()
        completion.complete.return_value = {
            "details": [
                {
                    "text": "월 금액은 999원이에요.",
                    "clause_id": clauses[0].id,
                    "quote": clauses[0].text,
                }
            ]
        }
        explainer = GroundedPolicyExplainer(completion, purpose="review")
        with self.assertRaisesRegex(ValueError, "explanation_evidence_mismatch"):
            await explainer.generate(request, clauses, timeout_seconds=5.0)
        messages = completion.complete.call_args.args[0]
        user = json.loads(messages[1]["content"])
        self.assertEqual(user["condition_results"], {})
        self.assertEqual(set(user), {"policy_name", "amount_won", "required_lead", "condition_results", "clauses"})
        dumped = json.dumps(messages, ensure_ascii=False)
        self.assertNotIn("source_url", dumped)
        self.assertNotIn("special", dumped)
        self.assertNotIn("parents", dumped)
        self.assertNotIn("profile", dumped)
