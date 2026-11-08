"""Fictional approvals only; never load local notices or actual review records."""

import asyncio
import copy
import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from backend.integration.f12_review.__main__ import create_preview_app
from backend.integration.f12_review.artifacts import load_approved_export
from backend.integration.f12_review.database import DraftEvidence, PreviewPolicy, require_local_test_database
from benefitfinder.application.policy_explanations import GeneratedExplanation, PublishedClause

TEST_URL = "postgresql://fictional@localhost/fictional_review_test"
AT = datetime(2000, 1, 1, tzinfo=timezone.utc)


def virtual_export(policy_id: str = "virtual-approved") -> dict:
    digest = hashlib.sha256(b"fictional source").hexdigest()
    return {
        "format_version": 1, "kind": "f12_approved_review_export", "pair_sha256": "a" * 64,
        "policy_id": policy_id,
        "source": {"id": "virtual-notice", "url": "https://policy.example/fictional-review",
                   "document_version": "sha256:" + digest, "content_sha256": digest,
                   "collected_at": AT.isoformat()},
        "items": [{"item_id": "timing:1", "kind": "timing",
                   "approval": {"reviewer": "fictional-reviewer", "reason": "가상 정기일 근거만 승인",
                                "reviewed_at": AT.isoformat()},
                   "candidate": {"evidence": {"document_id": "virtual-notice",
                       "document_version": "sha256:" + digest, "content_sha256": digest,
                       "location": "line 1", "quote": "가상 안내의 지급 조건을 확인합니다."}}}],
    }


def read_virtual_export(data: dict):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "approved.json"
        path.write_text(json.dumps(data, ensure_ascii=False))
        return load_approved_export(path, name="가상 검수 정책", category="employment")


class ApprovedExportTests(unittest.TestCase):
    def test_preserves_scope_identity_hash_and_original_citation(self):
        payload = virtual_export()
        doc = read_virtual_export(payload)
        self.assertEqual(doc.raw_policy_id, payload["policy_id"])
        self.assertEqual(doc.policy_id, "youth:" + payload["policy_id"])
        self.assertEqual(doc.items[0].reason, payload["items"][0]["approval"]["reason"])
        self.assertEqual(doc.items[0].quote, payload["items"][0]["candidate"]["evidence"]["quote"])

    def test_refuses_pending_rejected_missing_approval_and_duplicate_items(self):
        mutations = []
        for kind in ("needs_review", "rejected"):
            payload = virtual_export()
            payload["kind"] = kind
            mutations.append(payload)
        payload = virtual_export()
        del payload["items"][0]["approval"]
        mutations.append(payload)
        payload = virtual_export()
        payload["items"].append(copy.deepcopy(payload["items"][0]))
        mutations.append(payload)
        payload = virtual_export()
        payload["items"][0]["candidate"]["evidence"]["content_sha256"] = "b" * 64
        mutations.append(payload)
        payload = virtual_export("youth:example:P1")
        mutations.append(payload)
        payload = virtual_export()
        payload["items"][0]["approval"]["reviewed_at"] = "1999-01-01T00:00:00+00:00"
        mutations.append(payload)
        for payload in mutations:
            with self.subTest(payload=payload["kind"]), self.assertRaises(ValueError):
                read_virtual_export(payload)

    def test_refuses_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text('{"kind":"a","kind":"b"}')
            with self.assertRaisesRegex(ValueError, "invalid_f12_approved_export"):
                load_approved_export(path, name="가상", category="housing")

    def test_requires_explicit_local_test_database(self):
        for url in ("", "postgresql://localhost/production",
                    "postgresql://remote.example/anything_test",
                    "postgresql://localhost/anything_test?hostaddr=192.0.2.1",
                    "postgresql://localhost/anything_test?service=external",
                    "postgresql://localhost,remote.example/anything_test"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                require_local_test_database(url)
        self.assertEqual(require_local_test_database(TEST_URL), TEST_URL)
        with patch.dict("os.environ", {"PGHOSTADDR": "192.0.2.1"}), self.assertRaises(ValueError):
            require_local_test_database(TEST_URL)


class PreviewResponseTests(unittest.TestCase):
    def setUp(self):
        self.policy = PreviewPolicy("youth:virtual-approved", "가상 검수 정책", "employment",
                                    "v1", "https://policy.example/fictional-review", AT,
                                    "가상 승인 범위만. 실제 자격 미확정.", AT)
        self.clause = PublishedClause("timing:1", self.policy.policy_id, "v1", "가상 지급 조건",
                                     "가상 안내를 확인합니다.", self.policy.url, ("timing",),
                                     False, "line 1", AT)
        self.payload = {"domain": "youth", "profile": {
            "income_band": "between_15m_30m",
            "domain_data": {"interests": ["employment"], "special": ["none"]}}}

    def call(self, generator, *, timeout=5, evidence=None):
        app = create_preview_app(TEST_URL, (self.policy,), generator, timeout_seconds=timeout)
        evidence = evidence if evidence is not None else DraftEvidence((self.clause,), self.policy.scope)
        with patch("backend.integration.f12_review.__main__.load_approved_draft_evidence",
                   AsyncMock(return_value=evidence)):
            with TestClient(app) as client:
                response = client.post("/api/v1/recommendations", json=self.payload)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_real_id_preview_has_no_portfolios_or_confirmed_amounts_and_matches_contract(self):
        generator = AsyncMock()
        generator.generate.return_value = GeneratedExplanation("원문 조건을 확인해요.", ("timing:1",))
        result = self.call(generator)
        self.assertTrue(result["ai_generated"])
        request = generator.generate.call_args.args[0]
        self.assertEqual(request.conditions, ())
        self.assertIsNone(request.amount_won)
        for views in result["results"].values():
            for view in views.values():
                self.assertEqual(view["portfolios"], [])
                row = view["not_yet_reviewed"][0]
                self.assertEqual(row["policy_id"], self.policy.policy_id)
                self.assertTrue(row["verification_only"])
                self.assertEqual(row["eligibility_summary"], "needs_review")
                self.assertIsNone(row["review_explanation"]["amount_in_period_won"])
                self.assertEqual(row["review_explanation"]["ai_sources"][0]["quote"], self.clause.text)
        paths = Path(__file__).resolve().parents[2] / "contracts/v1"
        schemas = [json.loads(p.read_text()) for p in paths.glob("*.schema.json")]
        registry = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in schemas)
        schema = next(s for s in schemas if s["title"] == "Recommendation response")
        Draft202012Validator(schema, registry=registry, format_checker=FormatChecker()).validate(result)

    def test_generation_failure_and_timeout_keep_scope_and_fallback_without_evidence(self):
        async def slow(*args, **kwargs):
            await asyncio.sleep(0.1)
        for effect, budget in ((RuntimeError("fictional"), 5), (slow, 0.01)):
            generator = AsyncMock()
            generator.generate.side_effect = effect
            result = self.call(generator, timeout=budget)
            row = result["results"]["1y"]["pv_off"]["not_yet_reviewed"][0]
            self.assertFalse(row["review_explanation"]["ai_generated"])
            self.assertEqual(row["review_explanation"]["ai_sources"], [])
            self.assertEqual(row["review_scope"], self.policy.scope)
            self.assertEqual(row["eligibility_summary"], "needs_review")

    def test_revoked_rows_disappear_and_current_scope_is_not_taken_from_stale_catalog(self):
        result = self.call(None, evidence=DraftEvidence((), ""))
        self.assertEqual(result["results"]["1y"]["pv_off"]["not_yet_reviewed"], [])
        result = self.call(None, evidence=DraftEvidence((self.clause,), "현재 가상 승인 범위"))
        self.assertEqual(result["results"]["1y"]["pv_off"]["not_yet_reviewed"][0]["review_scope"],
                         "현재 가상 승인 범위")

    def test_explicit_focus_reads_only_requested_approved_item(self):
        clause = replace(self.clause, id="eligibility:2", text="가상 주소 유지 조건을 확인합니다.")
        generator = AsyncMock()
        generator.generate.return_value = GeneratedExplanation("주소 조건을 확인해요.", (clause.id,))
        app = create_preview_app(TEST_URL, (self.policy,), generator,
                                 focus_items={self.policy.policy_id: clause.id})
        evidence = DraftEvidence((clause,), "가상 주소 조건만 승인")
        with patch("backend.integration.f12_review.__main__.load_approved_draft_evidence",
                   AsyncMock(return_value=evidence)) as load:
            with TestClient(app) as client:
                response = client.post("/api/v1/recommendations", json=self.payload)
        self.assertEqual(response.status_code, 200)
        load.assert_awaited_once_with(TEST_URL, self.policy, item_ids=(clause.id,))
        self.assertEqual(generator.generate.call_args.args[1], (clause,))
        row = response.json()["results"]["1y"]["pv_off"]["not_yet_reviewed"][0]
        self.assertEqual(row["review_scope"], evidence.scope)
        self.assertEqual(row["review_explanation"]["ai_sources"][0]["quote"], clause.text)

    def test_document_selection_keeps_same_item_ids_in_distinct_sources(self):
        supplement = replace(self.policy, version="v2", url="https://policy.example/supplement")
        clause = replace(self.clause, document_version="v2", source_url=supplement.url)
        generator = AsyncMock()
        generator.generate.return_value = GeneratedExplanation("보충 조건을 확인해요.", (clause.id,))
        app = create_preview_app(
            TEST_URL, (self.policy, supplement), generator,
            focus_documents={self.policy.policy_id: "v2"},
            focus_items={self.policy.policy_id: clause.id},
        )
        with patch("backend.integration.f12_review.__main__.load_approved_draft_evidence",
                   AsyncMock(return_value=DraftEvidence((clause,), "보충 문서 승인 범위"))) as load:
            with TestClient(app) as client:
                result = client.post("/api/v1/recommendations", json=self.payload).json()
        load.assert_awaited_once_with(TEST_URL, supplement, item_ids=(clause.id,))
        rows = result["results"]["1y"]["pv_off"]["not_yet_reviewed"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["review_explanation"]["source"]["document_version"], "v2")
        source = rows[0]["review_explanation"]["ai_sources"][0]["source"]
        self.assertEqual(source["url"], supplement.url)
        self.assertEqual(source["document_version"], "v2")
        # Revocation of the selected document cannot fall back to the original notice.
        with patch("backend.integration.f12_review.__main__.load_approved_draft_evidence",
                   AsyncMock(side_effect=ValueError("invalid_preview_item_selection"))) as load:
            with TestClient(app) as client:
                result = client.post("/api/v1/recommendations", json=self.payload).json()
        load.assert_awaited_once_with(TEST_URL, supplement, item_ids=(clause.id,))
        self.assertEqual(result["results"]["1y"]["pv_off"]["not_yet_reviewed"], [])
        self.assertEqual(generator.generate.await_count, 1)

    def test_ambiguous_missing_or_duplicate_document_configuration_fails(self):
        supplement = replace(self.policy, version="v2")
        for policies, focus in (
            ((self.policy, supplement), {}),
            ((self.policy, supplement), {self.policy.policy_id: "v3"}),
            ((self.policy,), {"unknown-policy": "v1"}),
            ((self.policy, self.policy), {}),
        ):
            with self.subTest(focus=focus), self.assertRaisesRegex(
                ValueError, "invalid_review_preview_document"
            ):
                create_preview_app(TEST_URL, policies, None, focus_documents=focus)

    def test_focus_configuration_rejects_unknown_policy_and_invalid_item(self):
        for focus in ({"unknown-policy": "amount:2"}, {self.policy.policy_id: "amount:0"},
                      {self.policy.policy_id: "anything"}):
            with self.subTest(focus=focus), self.assertRaisesRegex(
                ValueError, "invalid_review_preview_focus"
            ):
                create_preview_app(TEST_URL, (self.policy,), None, focus_items=focus)

    def test_missing_focused_item_does_not_fall_back_to_unrelated_approved_scope(self):
        generator = AsyncMock()
        app = create_preview_app(TEST_URL, (self.policy,), generator,
                                 focus_items={self.policy.policy_id: "amount:2"})
        with patch("backend.integration.f12_review.__main__.load_approved_draft_evidence",
                   AsyncMock(side_effect=ValueError("invalid_preview_item_selection"))):
            with TestClient(app) as client:
                response = client.post("/api/v1/recommendations", json=self.payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"]["1y"]["pv_off"]["not_yet_reviewed"], [])
        generator.generate.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
