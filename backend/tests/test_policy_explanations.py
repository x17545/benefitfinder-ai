"""F13 explanation boundary tests (synthetic only)."""

import asyncio
import unittest
from dataclasses import replace

from benefitfinder.application.policy_explanations import (
    ExplanationRequest,
    GeneratedExplanation,
    PublishedClause,
    explain_policy,
    explanation_lead,
    safe_condition_results,
)


def _req():
    return ExplanationRequest(
        policy_id="youth:job-1",
        policy_name="[가상] 취업 지원",
        category="employment",
        document_version="v1",
        amount_won=1000,
        conditions=(("age", "met"),),
    )


def _clause(cid="c1", **over):
    kw = dict(
        id=cid,
        policy_id="youth:job-1",
        document_version="v1",
        title="가상 자격",
        text="가상 본문",
        source_url="https://example.invalid/a",
        topics=("employment",),
        published=True,
    )
    kw.update(over)
    return PublishedClause(**kw)


class _Repo:
    def __init__(self, clauses):
        self.clauses = clauses
        self.calls = 0

    async def search(self, request, *, limit):
        self.calls += 1
        return self.clauses


class _Gen:
    def __init__(self, mode="ok"):
        self.mode = mode
        self.calls = 0

    async def generate(self, request, clauses, *, timeout_seconds):
        self.calls += 1
        if self.mode == "boom":
            raise RuntimeError("model down")
        if self.mode == "slow":
            await asyncio.sleep(timeout_seconds + 0.05)
        if self.mode == "ghost":
            return GeneratedExplanation(text="설명", source_ids=("ghost",))
        return GeneratedExplanation(text="근거 있는 설명", source_ids=(clauses[0].id,))


class ExplainPolicyTest(unittest.IsolatedAsyncioTestCase):
    async def test_search_every_call_and_none_model_fallback(self):
        repo = _Repo((_clause(),))
        outs = [await explain_policy(_req(), repo, None) for _ in range(2)]
        self.assertEqual(repo.calls, 2)
        for got in outs:
            self.assertFalse(got.ai_generated)
            self.assertEqual(got.fallback_reason, "model_unconfigured")

    async def test_bad_evidence_blocks_generator(self):
        cases = [
            (),
            ((_clause(published=False),)),
            ((_clause(policy_id="youth:other"),)),
            ((_clause(document_version="v9"),)),
        ]
        for clauses in cases:
            with self.subTest(clauses=clauses):
                gen = _Gen()
                got = await explain_policy(_req(), _Repo(clauses), gen)
                self.assertFalse(got.ai_generated)
                self.assertEqual(gen.calls, 0)

    async def test_model_error_and_short_deadline_fall_back(self):
        ok = (_clause(),)
        err = await explain_policy(_req(), _Repo(ok), _Gen("boom"))
        self.assertFalse(err.ai_generated)
        self.assertEqual(err.fallback_reason, "generation_failed")
        late = await explain_policy(_req(), _Repo(ok), _Gen("slow"), timeout_seconds=0.02)
        self.assertFalse(late.ai_generated)
        self.assertEqual(late.fallback_reason, "timeout")

    async def test_search_is_included_in_deadline_and_failure_falls_back(self):
        class SlowRepo:
            async def search(self, request, *, limit):
                await asyncio.sleep(1)
                return (_clause(),)

        class FailedRepo:
            async def search(self, request, *, limit):
                raise RuntimeError("virtual provider details must not escape")

        generator = _Gen()
        result = await explain_policy(_req(), SlowRepo(), generator, timeout_seconds=0.01)
        self.assertEqual(result.fallback_reason, "timeout")
        self.assertFalse(result.ai_generated)
        self.assertEqual(generator.calls, 0)
        failed = await explain_policy(_req(), FailedRepo(), generator)
        self.assertEqual(failed.fallback_reason, "retrieval_failed")
        self.assertNotIn("virtual provider", failed.ai_reason)

    async def test_unretrieved_ids_rejected(self):
        got = await explain_policy(_req(), _Repo((_clause(),)), _Gen("ghost"))
        self.assertFalse(got.ai_generated)
        self.assertEqual(got.fallback_reason, "invalid_generation")

    def test_safe_results_drop_sensitive_and_validate(self):
        got = safe_condition_results(
            {
                "age": "met",
                "region": "not_met",
                "income": "met",
                "assets": "met",
                "special": "met",
                "parents": "met",
                "raw": "x",
            }
        )
        self.assertEqual(got, (("age", "met"), ("region", "not_met")))
        with self.assertRaises(ValueError):
            safe_condition_results({"age": "maybe"})

    def test_unknown_amount_and_unmet_status_are_never_inferred(self):
        request = replace(_req(), amount_won=None, conditions=(("age", "not_met"),))
        text = explanation_lead(request)
        self.assertIn("미충족인 일반 조건", text)
        self.assertIn("지원 금액은 확인이 필요해요", text)
        self.assertNotIn("1,000", text)
        with self.assertRaisesRegex(ValueError, "^invalid_explanation_request$"):
            replace(request, conditions=(("income", "met"),))
