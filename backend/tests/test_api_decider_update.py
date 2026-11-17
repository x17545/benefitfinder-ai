"""Fictional API/native outputs test storage preparation, never model accuracy."""

from copy import deepcopy
from dataclasses import replace
from datetime import date
import hashlib
import json
import unittest

from benefitfinder.application.decider import MODEL_ID, MODEL_REVISION
from benefitfinder.infrastructure.policy_catalog_records import CatalogRecord
from benefitfinder.infrastructure.unreviewed_catalog import validate_unreviewed
from benefitfinder.ingestion.api_decider_update import (
    QUESTION_VERSION,
    compile_api_decider_update,
    decider_update_summary,
)
from backend.tests.test_decider_pending import case


def artifact(value):
    text = value.documents[0].text
    start = text.index("20만원") if "20만원" in text else 0
    end = start + 4
    return {
        "question_version": QUESTION_VERSION,
        "model": {"id": MODEL_ID, "revision": MODEL_REVISION, "provider": "local", "fast": "false"},
        "source_projection": text,
        "source_sha256": value.documents[0].content_sha256,
        "material_sha256": value.collection_sources[0].material_sha256,
        "extraction_spans": [
            {
                "kind": "amount",
                "field": "plcySprtCn",
                "start": start,
                "end": end,
                "text": text[start:end],
                "score": 0.2,
            }
        ],
        "decisions": [
            {"answers": {"amount_kind": {"choice": "cap"}, "overlap": {"choice": "restricted"}}}
        ],
    }


class ApiDeciderUpdateTest(unittest.TestCase):
    def test_full_f12_summary_is_preserved_while_public_analysis_is_compact(self):
        value, _, _ = case()
        output = artifact(value)
        summary = decider_update_summary(output, artifact_ref="private-fictional/result.json")
        self.assertEqual(summary["evidence"], output["extraction_spans"])
        self.assertEqual(summary["decisions"], [output["decisions"][0]["answers"]])
        self.assertEqual(summary["ai_review_calls"], 0)
        private_summary = deepcopy(summary)
        result = compile_api_decider_update(value, summary, as_of=date(2026, 11, 17))
        self.assertEqual(summary, private_summary)
        record = CatalogRecord.model_validate(result.payload)
        validate_unreviewed(record, value.documents[0].text)
        public = record.benefit.domain_data["decider_analysis"]
        canonical = json.dumps(
            private_summary,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        self.assertEqual(public["summary_sha256"], hashlib.sha256(canonical).hexdigest())
        self.assertEqual(public["evidence_count"], 1)
        self.assertEqual(public["decision_count"], 1)
        self.assertEqual(public["artifact_ref"], "private-fictional/result.json")
        self.assertEqual(public["model"], output["model"])
        self.assertEqual(public["source_sha256"], value.documents[0].content_sha256)
        self.assertEqual(public["material_sha256"], value.collection_sources[0].material_sha256)
        self.assertNotIn("evidence", public)
        self.assertNotIn("decisions", public)
        encoded_public = json.dumps(public, ensure_ascii=False)
        self.assertNotIn("20만원", encoded_public)
        self.assertNotIn("restricted", encoded_public)
        self.assertLess(len(encoded_public.encode()), 2048)
        self.assertFalse(record.benefit.reviewed)
        self.assertIsNone(record.payments)
        self.assertIsNone(record.benefit.benefit_amount.amount_won)
        expected_version = (
            "sha256:" + value.collection_sources[0].material_sha256 + ":decider-compact-v1"
        )
        self.assertEqual(record.benefit.source.document_version, expected_version)
        self.assertEqual(record.benefit.source.url, value.documents[0].source_url)
        self.assertEqual(
            record.benefit.source.checked_at, value.documents[0].collected_at.isoformat()
        )
        self.assertEqual(record.source_sha256, value.documents[0].content_sha256)
        self.assertEqual(
            record.collection_sources[0].material_sha256,
            value.collection_sources[0].material_sha256,
        )
        self.assertTrue(record.benefit.eligibility_conditions)
        self.assertTrue(all(rule.kind == "manual" for rule in record.conditions))
        for condition in record.benefit.eligibility_conditions:
            self.assertTrue(condition.evidence)
            for evidence in condition.evidence:
                start, end = map(int, evidence.location.removeprefix("chars:").split("-"))
                self.assertEqual(value.documents[0].text[start:end], evidence.quote)
                self.assertEqual(evidence.source, record.benefit.source)

    def test_api_policy_without_cash_amount_can_still_be_loaded(self):
        value, _, _ = case(fields={"plcySprtCn": "구직 상담과 채용 정보 제공"})
        summary = decider_update_summary(
            artifact(value), artifact_ref="private-fictional/result.json"
        )
        result = compile_api_decider_update(value, summary, as_of=date(2026, 11, 17))
        self.assertIsNotNone(result.payload)
        self.assertEqual(result.payload["benefit"]["benefit_amount"]["amount_status"], "unknown")

    def test_raw_api_title_trailing_space_is_preserved_after_stripped_identity_match(self):
        raw_title = "가상 청년 월세 지원 "
        value, _, _ = case(fields={"plcyNm": raw_title})
        value = replace(
            value,
            policies=(replace(value.policies[0], name=value.policies[0].name.strip()),),
        )
        summary = decider_update_summary(
            artifact(value), artifact_ref="private-fictional/result.json"
        )
        result = compile_api_decider_update(value, summary, as_of=date(2026, 11, 17))
        self.assertIsNotNone(result.payload)
        self.assertEqual(result.payload["benefit"]["title"], raw_title)
        record = CatalogRecord.model_validate(result.payload)
        self.assertEqual(record.benefit.title, raw_title)
        validate_unreviewed(record, value.documents[0].text)

    def test_changed_source_and_false_coordinates_are_rejected(self):
        value, _, _ = case()
        for change in ("source", "coordinate", "model", "fast"):
            with self.subTest(change=change):
                output = deepcopy(artifact(value))
                if change == "source":
                    output["source_projection"] += "변경"
                elif change == "coordinate":
                    output["extraction_spans"][0]["text"] = "허위 금액"
                elif change == "model":
                    output["model"]["revision"] = "unrecognized"
                else:
                    output["model"]["fast"] = "true"
                with self.assertRaises(ValueError):
                    decider_update_summary(output, artifact_ref="private-fictional/result.json")

    def test_scope_is_kept_separate_from_model_analysis_completion(self):
        value, _, _ = case(fields={"lclsfNm": "일자리,교육", "mclsfNm": "취업"})
        summary = decider_update_summary(
            artifact(value), artifact_ref="private-fictional/result.json"
        )
        result = compile_api_decider_update(value, summary, as_of=date(2026, 11, 17))
        self.assertIsNone(result.payload)
        self.assertEqual(result.reasons, ("api_decider_catalog_outside_mvp",))
        self.assertTrue(result.normalized["analysis_complete"])


if __name__ == "__main__":
    unittest.main()
