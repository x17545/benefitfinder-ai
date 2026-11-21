"""Offline tests: rebuilding published unreviewed services and exact retirement."""

from __future__ import annotations

import sys
import types
import unittest
from unittest import mock

from benefitfinder.infrastructure.ai_catalog_review import project_youthcenter_source
from benefitfinder.infrastructure.service_catalog_records import service_record_hash
from benefitfinder.infrastructure.unreviewed_catalog import (
    load_published_unreviewed_services,
    retire_unreviewed_releases,
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


def _row(fields=FIELDS, material=MATERIAL, missing=None, approved=None, source_fields=FIELDS):
    published = record(project_youthcenter_source(source_fields))
    digest = approved if approved is not None else service_record_hash(published)
    return (published.model_dump(mode="json"), digest, fields, material, missing)


class LoadPublishedUnreviewedServicesTest(unittest.TestCase):
    def test_rebuilds_exact_published_pair(self) -> None:
        connection = _Connection([_row()])

        services, skipped = load_published_unreviewed_services(connection)

        self.assertEqual(skipped, [])
        self.assertEqual(len(services), 1)
        service = services[0]
        self.assertFalse(service.rebased)
        self.assertEqual(service.source, project_youthcenter_source(FIELDS))
        self.assertEqual(service.approved_sha256, service_record_hash(service.record))
        self.assertIn("publication_status = 'published'", connection.queries[0])
        self.assertIn("review_type = 'unreviewed'", connection.queries[0])

    def test_changed_projection_with_all_citations_is_rebased(self) -> None:
        current = {**FIELDS, "plcyExplnCn": "[가상] 설명 필드가 나중에 추가됨"}
        services, skipped = load_published_unreviewed_services(_Connection([_row(fields=current)]))

        self.assertEqual(skipped, [])
        self.assertTrue(services[0].rebased)
        self.assertEqual(services[0].source, project_youthcenter_source(current))

    def test_missing_citation_is_skipped_not_guessed(self) -> None:
        current = {**FIELDS, "plcySprtCn": "[가상] 다른 내용"}
        services, skipped = load_published_unreviewed_services(_Connection([_row(fields=current)]))

        self.assertEqual(services, [])
        self.assertEqual(skipped[0]["reason"], "source_quotes_missing")

    def test_unusable_releases_are_skipped_with_reason(self) -> None:
        cases = {
            "approved_hash_mismatch": _row(approved="c" * 64),
            "collected_source_missing": _row(fields=None, material=None),
            "collected_source_marked_missing": _row(missing="2026-11-18T00:00:00+00:00"),
            "collected_source_changed": _row(material="b" * 64),
        }
        for reason, row in cases.items():
            with self.subTest(reason=reason):
                services, skipped = load_published_unreviewed_services(_Connection([row]))
                self.assertEqual(services, [])
                self.assertEqual(skipped, [{"policy_id": "youth:" + POLICY_NO, "reason": reason}])


class _Result:
    def __init__(self, rowcount):
        self.rowcount = rowcount


class _WriteConnection:
    def __init__(self, rowcounts):
        self.rowcounts = list(rowcounts)
        self.updates: list[tuple] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=None):
        if query.startswith("UPDATE"):
            self.updates.append(params)
            return _Result(self.rowcounts.pop(0))
        return _Result(1)


class RetireUnreviewedReleasesTest(unittest.TestCase):
    def _run(self, releases, rowcounts):
        connection = _WriteConnection(rowcounts)
        fake = types.SimpleNamespace(connect=lambda *args, **kwargs: connection)
        with mock.patch.dict(sys.modules, {"psycopg": fake}):
            retire_unreviewed_releases("postgresql://fixture.invalid/db", releases)
        return connection

    def test_retires_exact_releases_in_policy_order(self) -> None:
        connection = self._run(
            [("youth:2", "sha256:" + "b" * 64, "2" * 64), ("youth:1", "sha256:" + "a" * 64, "1" * 64)],
            [1, 1],
        )
        self.assertEqual([update[0] for update in connection.updates], ["youth:1", "youth:2"])

    def test_any_non_current_release_fails_the_batch(self) -> None:
        with self.assertRaisesRegex(ValueError, "unreviewed_release_not_current"):
            self._run([("youth:1", "sha256:" + "a" * 64, "1" * 64)], [0])

    def test_invalid_batches_are_rejected_before_connecting(self) -> None:
        bad = [
            [],
            [("youth:1", "v", "1" * 64), ("youth:1", "v", "2" * 64)],
            [("youth:1", "v", "not-a-digest")],
            [("", "v", "1" * 64)],
        ]
        for releases in bad:
            with self.subTest(releases=releases):
                with self.assertRaisesRegex(ValueError, "invalid_unreviewed_release_batch"):
                    retire_unreviewed_releases("postgresql://fixture.invalid/db", releases)


if __name__ == "__main__":
    unittest.main()
