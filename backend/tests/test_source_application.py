from datetime import date
import unittest

from benefitfinder.ingestion.source_application import source_application


class SourceApplicationTest(unittest.TestCase):
    def test_official_continuous_code_does_not_invent_dates(self):
        result, issues = source_application({"aplyPrdSeCd": "0057002"}, as_of=date(2026, 11, 16))
        self.assertEqual(result["status"], "open")
        self.assertIsNone(result["starts_on"])
        self.assertIsNone(result["ends_on"])
        self.assertEqual(issues, ())

    def test_closed_code_is_not_continuous(self):
        result, _ = source_application({"aplyPrdSeCd": "0057003"}, as_of=date(2026, 11, 16))
        self.assertEqual(result["status"], "closed")

    def test_application_dates_are_distinct_from_business_dates(self):
        result, _ = source_application({
            "aplyPrdSeCd": "0057001", "aplyYmd": "20261101 ~ 20261231",
            "bizPrdEndYmd": "20251231",
        }, as_of=date(2026, 11, 16))
        self.assertEqual(result["status"], "open")
        self.assertEqual(result["ends_on"], "2026-12-31")

    def test_expired_continuous_business_requires_current_notice(self):
        result, issues = source_application({
            "aplyPrdSeCd": "0057002", "bizPrdEndYmd": "20251231",
        }, as_of=date(2026, 11, 16))
        self.assertEqual(result["status"], "unknown")
        self.assertIn("continuous_notice_business_period_ended", issues)

    def test_invalid_or_unspecified_range_does_not_become_open(self):
        for raw in ("20261301 ~ 20261231", "", "20261101 ~ 예산 소진"):
            result, issues = source_application({"aplyPrdSeCd": "0057001", "aplyYmd": raw},
                                                as_of=date(2026, 11, 16))
            self.assertEqual(result["status"], "unknown")
            self.assertTrue(issues)

    def test_explicit_closure_overrides_an_unexpired_range(self):
        result, _ = source_application({"aplyPrdSeCd": "0057003", "aplyYmd": "20261101 ~ 20261231"},
                                        as_of=date(2026, 11, 16))
        self.assertEqual(result["status"], "closed")
