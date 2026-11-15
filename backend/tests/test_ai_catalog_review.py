"""AI provenance checks use fictional records and cannot publish by themselves."""

from datetime import datetime, timezone
import unittest

from pydantic import ValidationError

from benefitfinder.infrastructure.ai_catalog_review import AIReview, validate_ai_review
from benefitfinder.infrastructure.policy_catalog_records import CatalogRecord, record_hash
from backend.tests.catalog_fixtures import SOURCE_TEXT, release_data


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


if __name__ == "__main__":
    unittest.main()
