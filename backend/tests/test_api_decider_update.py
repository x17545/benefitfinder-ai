"""Fictional API/native outputs test storage preparation, never model accuracy."""

from copy import deepcopy
from datetime import date
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
    def test_native_outputs_are_retained_without_an_ai_review_or_quality_gate(self):
        value, _, _ = case()
        output = artifact(value)
        summary = decider_update_summary(output, artifact_ref="private-fictional/result.json")
        self.assertEqual(summary["evidence"], output["extraction_spans"])
        self.assertEqual(summary["decisions"], [output["decisions"][0]["answers"]])
        self.assertEqual(summary["ai_review_calls"], 0)
        result = compile_api_decider_update(value, summary, as_of=date(2026, 11, 17))
        record = CatalogRecord.model_validate(result.payload)
        validate_unreviewed(record, value.documents[0].text)
        self.assertFalse(record.benefit.reviewed)
        self.assertIsNone(record.payments)
        self.assertIsNone(record.benefit.benefit_amount.amount_won)
        self.assertEqual(record.benefit.domain_data["decider_analysis"], summary)
        self.assertTrue(record.benefit.source.document_version.endswith(":decider-v3"))

    def test_api_policy_without_cash_amount_can_still_be_loaded(self):
        value, _, _ = case(fields={"plcySprtCn": "구직 상담과 채용 정보 제공"})
        summary = decider_update_summary(
            artifact(value), artifact_ref="private-fictional/result.json"
        )
        result = compile_api_decider_update(value, summary, as_of=date(2026, 11, 17))
        self.assertIsNotNone(result.payload)
        self.assertEqual(result.payload["benefit"]["benefit_amount"]["amount_status"], "unknown")

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
