"""AI provenance checks use fictional records and cannot publish by themselves."""

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest

from pydantic import ValidationError

from benefitfinder.infrastructure.ai_catalog_review import (
    AIReview,
    _verify_real_source,
    project_youthcenter_source,
    publish_ai_batch,
    validate_ai_review,
)
from benefitfinder.infrastructure.policy_catalog_records import CatalogRecord, record_hash
from backend.tests.catalog_fixtures import SOURCE_TEXT, release_data


class SourceConnection:
    def __init__(self, observed):
        self.observed = observed

    def execute(self, statement, parameters):
        self.statement = statement
        self.parameters = parameters
        return self

    def fetchone(self):
        return self.observed


class AIReviewTest(unittest.TestCase):
    def setUp(self):
        self.record = CatalogRecord.model_validate(release_data())
        self.fields = {
            "writer_agent": "writer-1", "reviewer_agent": "reviewer-1", "reviewer_model": "test-model",
            "verdict": "pass", "reviewed_at": "2026-11-02T00:00:00+00:00",
            "document_version": "fictional-v1", "source_sha256": self.record.source_sha256,
            "record_sha256": record_hash(self.record),
        }
        self.now = datetime(2026, 11, 3, tzinfo=timezone.utc)

    def check(self, fields=None, source=SOURCE_TEXT):
        validate_ai_review(self.record, AIReview.model_validate(fields or self.fields), source_text=source, now=self.now)

    def test_pass_and_source_invalidation(self):
        self.check()
        with self.assertRaises(ValueError):
            self.check(source=SOURCE_TEXT + " 변경")

    def test_rejects_failed_pending_and_changed_record_or_source(self):
        for field, value in (("verdict", "revise"), ("verdict", "hold"), ("record_sha256", "0" * 64),
                             ("source_sha256", "0" * 64), ("document_version", "other")):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.check({**self.fields, field: value})

    def test_independent_reviewer_and_valid_times(self):
        with self.assertRaises(ValidationError):
            self.check({**self.fields, "reviewer_agent": "writer-1"})
        for value in ("2026-10-01T00:00:00+00:00", "2026-12-01T00:00:00+00:00"):
            with self.subTest(reviewed_at=value), self.assertRaises(ValueError):
                self.check({**self.fields, "reviewed_at": value})

    def test_real_source_accepts_only_exact_youthcenter_policy_ids(self):
        external_id = "20260101000000000001"
        material = "a" * 64
        title = "[가상] 준비 큐 정책"
        fields = {"plcyNo": external_id, "plcyNm": title, "plcySprtCn": "[가상] 지원 내용"}
        source_text = project_youthcenter_source(fields)
        binding = SimpleNamespace(
            source="youthcenter", external_id=external_id, material_sha256=material
        )

        def record(policy_id):
            benefit = SimpleNamespace(
                id=policy_id,
                title=title,
                virtual_data=False,
                source=SimpleNamespace(
                    url="https://www.youthcenter.go.kr/youthPolicy/ythPlcyTotalSearch/ythPlcyDetail/"
                    + external_id,
                    document_version="sha256:" + material,
                ),
            )
            return SimpleNamespace(benefit=benefit, collection_sources=(binding,))

        observed = (fields, material, None)
        for policy_id in ("youth:" + external_id, "youth:youthcenter:" + external_id):
            with self.subTest(policy_id=policy_id):
                _verify_real_source(SourceConnection(observed), record(policy_id), source_text)
        for policy_id in (
            "youth:other:" + external_id,
            "youth:" + external_id + "0",
            "youth:youthcenter:" + external_id + "0",
        ):
            with self.subTest(policy_id=policy_id), self.assertRaisesRegex(
                ValueError, "ai_catalog_source_projection_mismatch"
            ):
                _verify_real_source(SourceConnection(observed), record(policy_id), source_text)
        other_source = SimpleNamespace(
            source="gov24", external_id=external_id, material_sha256=material
        )
        record_with_other_source = record("youth:" + external_id)
        record_with_other_source.collection_sources = (other_source,)
        with self.assertRaisesRegex(ValueError, "ai_catalog_youthcenter_source_required"):
            _verify_real_source(SourceConnection(observed), record_with_other_source, source_text)

    def test_replacement_arguments_are_validated_before_database_access(self):
        entry = (self.record, AIReview.model_validate(self.fields), SOURCE_TEXT, None)
        previous = {self.record.benefit.id: record_hash(self.record)}
        with self.assertRaisesRegex(ValueError, "ai_catalog_replacement_not_allowed"):
            publish_ai_batch("unused", [entry], expected_previous=previous)
        for expected_previous in ({self.record.benefit.id: "0" * 63}, {"youth:other": "0" * 64}):
            with self.subTest(expected_previous=expected_previous), self.assertRaisesRegex(
                ValueError, "invalid_ai_catalog_expected_previous"
            ):
                publish_ai_batch(
                    "unused", [entry], allow_replace_ai=True, expected_previous=expected_previous
                )


if __name__ == "__main__":
    unittest.main()
