"""Offline tests for rebuilding published unreviewed service pairs from the DB."""

from __future__ import annotations

import unittest

from benefitfinder.infrastructure.ai_catalog_review import project_youthcenter_source
from benefitfinder.infrastructure.unreviewed_catalog import (
    load_published_unreviewed_services,
)

from backend.tests.test_service_source_facts import POLICY_NO, record

MATERIAL = "a" * 64
FIELDS = {
    "plcyNo": POLICY_NO,
    "plcyNm": "[가상] 남도 청년 보금자리",
    "plcySprtCn": "○ [가상] 청년, 신혼부부 등\n○ [가상] 공공임대주택 10호 신축",
    "zipCd": "48120,48170",
}


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Connection:
    def __init__(self, rows):
        self.rows = rows
        self.queries: list[str] = []

    def execute(self, query, params=None):
        self.queries.append(query)
        return _Rows(self.rows)


def _row(fields=FIELDS, material=MATERIAL, missing=None, payload=None):
    source = project_youthcenter_source(FIELDS)
    data = payload if payload is not None else record(source).model_dump(mode="json")
    return (data, fields, material, missing)


class LoadPublishedUnreviewedServicesTest(unittest.TestCase):
    def test_rebuilds_exact_published_pair(self) -> None:
        connection = _Connection([_row()])

        entries, skipped = load_published_unreviewed_services(connection)

        self.assertEqual(skipped, [])
        self.assertEqual(len(entries), 1)
        rebuilt, source = entries[0]
        self.assertEqual(source, project_youthcenter_source(FIELDS))
        self.assertEqual(rebuilt.benefit.id, "youth:" + POLICY_NO)
        self.assertIn("publication_status = 'published'", connection.queries[0])
        self.assertIn("review_type = 'unreviewed'", connection.queries[0])

    def test_missing_or_changed_sources_are_skipped_with_reason(self) -> None:
        cases = {
            "collected_source_missing": _row(fields=None, material=None),
            "collected_source_marked_missing": _row(missing="2026-11-18T00:00:00+00:00"),
            "collected_source_changed": _row(material="b" * 64),
        }
        for reason, row in cases.items():
            with self.subTest(reason=reason):
                entries, skipped = load_published_unreviewed_services(_Connection([row]))
                self.assertEqual(entries, [])
                self.assertEqual(skipped, [{"policy_id": "youth:" + POLICY_NO, "reason": reason}])

    def test_changed_projection_is_skipped_not_guessed(self) -> None:
        changed = {**FIELDS, "plcySprtCn": FIELDS["plcySprtCn"] + " (수정)"}
        entries, skipped = load_published_unreviewed_services(_Connection([_row(fields=changed)]))

        self.assertEqual(entries, [])
        self.assertEqual(skipped[0]["reason"], "source_projection_mismatch")


if __name__ == "__main__":
    unittest.main()
