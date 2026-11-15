"""Tests for conservative filtering and candidate-only duplicate detection."""

import unittest

from scripts.reconcile_youthcenter_candidates import reconcile


def record(policy_id: str, title: str, region: str, category: str) -> dict:
    return {"external_id": policy_id, "fields": {"plcyNm": title, "zipCd": region, "lclsfNm": category}}


class ReconcileTest(unittest.TestCase):
    def test_filters_exact_region_tokens_and_never_merges_duplicate_titles(self):
        data = [record("1", "청년 주택", "48121,11110", "주거"),
                record("2", "청년  주택", "48890", "일자리"),
                record("3", "다른 정책", "148121", "주거"),
                record("4", "다른 정책", "48121", "교육")]
        result = reconcile(data, ["1", "3", "missing"])
        self.assertEqual(result["candidate_ids"], ["1", "2"])
        self.assertEqual(result["same_title_candidates"], [["1", "2"]])
        self.assertEqual(result["prior_ids_missing_from_source"], ["missing"])
        self.assertEqual(result["prior_ids_outside_filter"], ["3"])
        self.assertFalse(result["scope_confirmed"])
        self.assertFalse(result["duplicates_confirmed"])

    def test_rejects_duplicate_source_or_prior_ids(self):
        row = record("1", "제목", "48121", "주거")
        with self.assertRaises(ValueError):
            reconcile([row, row], [])
        with self.assertRaises(ValueError):
            reconcile([row], ["1", "1"])


if __name__ == "__main__":
    unittest.main()
