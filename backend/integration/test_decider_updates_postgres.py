"""Bulk F12 and model-catalog stores against fictional PostgreSQL test data."""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import hashlib
from pathlib import Path
import unittest
from uuid import uuid4

import psycopg

from backend.integration.postgres_fixture import test_database_url
from backend.integration.test_policy_source_rechecks_postgres import dedicated_database
from backend.tests.test_api_decider_update import artifact
from backend.tests.test_decider_pending import CHECKED_AT, case
from benefitfinder.application.collected_policies import CollectedPolicyBatch
from benefitfinder.application.collection import CollectedRecord
from benefitfinder.application.policy_analysis import PolicyAnalysisStoreError
from benefitfinder.infrastructure.database.repositories.collected_policies import (
    PostgresCollectedPolicies,
)
from benefitfinder.infrastructure.database.repositories.decider_updates import (
    publish_model_catalog_updates,
    store_decider_analyses,
)
from benefitfinder.infrastructure.database.repositories.published_policies import publish_policy
from benefitfinder.infrastructure.policy_catalog_records import CatalogRecord, record_hash
from benefitfinder.ingestion.api_decider_update import (
    compile_api_decider_update,
    decider_update_summary,
)
from benefitfinder.ingestion.decider_pending import compile_pending_source

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations/versions"


class DeciderUpdatesPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database = dedicated_database(test_database_url())
        cls.url = cls.database.__enter__()
        with psycopg.connect(cls.url) as connection:
            for path in sorted(MIGRATIONS.glob("*.sql")):
                if "0007" <= path.name[:4] <= "0014":
                    connection.execute(path.read_text())

    @classmethod
    def tearDownClass(cls) -> None:
        cls.database.__exit__(None, None, None)

    def setUp(self) -> None:
        with psycopg.connect(self.url) as connection:
            connection.execute(
                """TRUNCATE public.f12_analysis_sources,public.f12_analysis_tasks,
                   public.policy_recheck_queue,public.policy_catalog_source_links,
                   public.policy_catalog_releases,public.collected_policy_changes,
                   public.collected_policies,public.collected_policy_runs,
                   public.f13_clauses,public.f13_policy_documents RESTART IDENTITY CASCADE"""
            )
        self.collected = PostgresCollectedPolicies(self.url)
        self.records: dict[str, CollectedRecord] = {}
        self.source_at = CHECKED_AT - timedelta(seconds=2)

    @staticmethod
    def material(label: str) -> str:
        return hashlib.sha256(("fictional:" + label).encode()).hexdigest()

    def put_source(
        self,
        external_id: str,
        material: str,
        fields: dict,
        *,
        at: datetime | None = None,
    ) -> None:
        if at is None:
            at = self.source_at
            self.source_at += timedelta(milliseconds=100)
        self.records[external_id] = CollectedRecord(
            "youthcenter",
            external_id,
            material,
            "fictional-snapshot/" + external_id,
            at - timedelta(milliseconds=100),
            fields,
        )
        self.collected.save(
            CollectedPolicyBatch(
                "youthcenter",
                uuid4().hex,
                at,
                tuple(self.records.values()),
            )
        )

    def analysis(self, external_id: str, material: str, *, title: str | None = None):
        policy_id = "youth:youthcenter:" + external_id
        value, _, fields = case(
            fields={"plcyNo": external_id, "plcyNm": title or "가상 정책 " + external_id},
            policy_id=policy_id,
        )
        document = replace(
            value.documents[0],
            id="youthcenter:" + external_id + ":api-decider-v2",
            source_url=(
                "https://www.youthcenter.go.kr/youthPolicy/ythPlcyTotalSearch/"
                "ythPlcyDetail/" + external_id
            ),
            document_version="sha256:" + material,
        )
        source = replace(
            value.collection_sources[0],
            external_id=external_id,
            material_sha256=material,
        )
        return replace(value, documents=(document,), collection_sources=(source,)), fields

    @staticmethod
    def summary(value, name: str) -> dict:
        return decider_update_summary(artifact(value), artifact_ref="private-fictional/" + name)

    def catalog_record(self, value, summary: dict) -> CatalogRecord:
        compilation = compile_api_decider_update(value, summary, as_of=date(2026, 11, 17))
        self.assertIsNotNone(compilation.payload)
        return CatalogRecord.model_validate(compilation.payload)

    def pending_record(self, value) -> CatalogRecord:
        compilation = compile_pending_source(value)
        self.assertIsNotNone(compilation.payload)
        return CatalogRecord.model_validate(compilation.payload)

    def test_f12_batch_insert_and_replay(self) -> None:
        first, fields_a = self.analysis("1001", self.material("a"))
        second, fields_b = self.analysis("1002", self.material("b"))
        self.put_source("1001", first.collection_sources[0].material_sha256, fields_a)
        self.put_source("1002", second.collection_sources[0].material_sha256, fields_b)
        entries = [
            (first, self.summary(first, "a.json"), "1" * 64),
            (second, self.summary(second, "b.json"), "2" * 64),
        ]

        self.assertEqual(
            store_decider_analyses(self.url, entries),
            {"inserted": 2, "replayed": 0},
        )
        self.assertEqual(
            store_decider_analyses(self.url, entries),
            {"inserted": 0, "replayed": 2},
        )
        with psycopg.connect(self.url) as connection:
            rows = connection.execute(
                """SELECT policy_id,status,attempts,analysis_version,output_ref,
                          output_sha256,result_summary
                   FROM public.f12_analysis_tasks ORDER BY policy_id"""
            ).fetchall()
            source_count = connection.execute(
                "SELECT count(*) FROM public.f12_analysis_sources"
            ).fetchone()[0]
        self.assertEqual(len(rows), 2)
        self.assertEqual(source_count, 2)
        for row, expected_sha in zip(rows, ("1" * 64, "2" * 64), strict=True):
            self.assertEqual(row[1], "needs_review")
            self.assertEqual(row[2], 1)
            self.assertEqual(row[3], 8)
            self.assertRegex(row[4], r"^f12-analysis/[0-9]+/[0-9a-f]{32}$")
            self.assertEqual(row[5], expected_sha)
            self.assertTrue(row[6]["analysis_complete"])

    def test_f12_one_stale_source_rolls_back_whole_batch(self) -> None:
        first, fields_a = self.analysis("1101", self.material("a-old"))
        second, fields_b = self.analysis("1102", self.material("b"))
        old_material = first.collection_sources[0].material_sha256
        self.put_source("1101", old_material, fields_a)
        self.put_source("1102", second.collection_sources[0].material_sha256, fields_b)
        self.assertEqual(
            store_decider_analyses(self.url, [(first, self.summary(first, "old.json"), "3" * 64)]),
            {"inserted": 1, "replayed": 0},
        )
        with psycopg.connect(self.url) as connection:
            before = connection.execute(
                "SELECT task_id,output_sha256 FROM public.f12_analysis_tasks"
            ).fetchall()

        changed = replace(
            first.documents[0],
            text=first.documents[0].text + "\n\n변경된 가상 원문",
        )
        changed_value = replace(
            first,
            documents=(changed,),
            collection_sources=(
                replace(
                    first.collection_sources[0],
                    material_sha256=self.material("a-new"),
                ),
            ),
        )
        stale_fields = dict(fields_a)
        stale_fields["plcySprtCn"] += " 변경된 가상 원문"
        self.put_source(
            "1101",
            changed_value.collection_sources[0].material_sha256,
            stale_fields,
            at=CHECKED_AT + timedelta(seconds=1),
        )

        with self.assertRaisesRegex(PolicyAnalysisStoreError, "f12_analysis_source_stale"):
            store_decider_analyses(
                self.url,
                [
                    (second, self.summary(second, "new.json"), "4" * 64),
                    (first, self.summary(first, "old.json"), "3" * 64),
                ],
            )
        with psycopg.connect(self.url) as connection:
            after = connection.execute(
                "SELECT task_id,output_sha256 FROM public.f12_analysis_tasks"
            ).fetchall()
            second_count = connection.execute(
                "SELECT count(*) FROM public.f12_analysis_tasks WHERE policy_id=%s",
                (second.policy_id,),
            ).fetchone()[0]
        self.assertEqual(after, before)
        self.assertEqual(second_count, 0)

    def test_catalog_atomic_replace_replay_and_cas_mismatch_rollback(self) -> None:
        value, fields = self.analysis("1201", self.material("replace"))
        self.put_source("1201", value.collection_sources[0].material_sha256, fields)
        text = value.documents[0].text
        old = self.pending_record(value)
        self.assertEqual(
            publish_model_catalog_updates(self.url, [(old, text, None)]),
            {"inserted": 1, "replaced": 0, "replayed": 0, "protected_existing": 0},
        )

        new = self.catalog_record(value, self.summary(value, "replace.json"))
        previous = record_hash(old)
        self.assertEqual(
            publish_model_catalog_updates(self.url, [(new, text, previous)]),
            {"inserted": 0, "replaced": 1, "replayed": 0, "protected_existing": 0},
        )
        self.assertEqual(
            publish_model_catalog_updates(self.url, [(new, text, previous)]),
            {"inserted": 0, "replaced": 0, "replayed": 1, "protected_existing": 0},
        )

        alternate = new.model_dump(mode="json")

        def change_versions(item):
            if isinstance(item, dict):
                return {
                    key: (
                        "sha256:" + value.collection_sources[0].material_sha256 + ":source-facts-v1"
                        if key == "document_version"
                        else change_versions(child)
                    )
                    for key, child in item.items()
                }
            if isinstance(item, list):
                return [change_versions(child) for child in item]
            return item

        mismatch = CatalogRecord.model_validate(change_versions(alternate))
        with self.assertRaisesRegex(ValueError, "catalog_replacement_conflict"):
            publish_model_catalog_updates(self.url, [(mismatch, text, "0" * 64)])
        with psycopg.connect(self.url) as connection:
            current = connection.execute(
                """SELECT approved_sha256,document_version,publication_status
                   FROM public.policy_catalog_releases WHERE policy_id=%s
                   ORDER BY publication_status='published' DESC,document_version""",
                (new.benefit.id,),
            ).fetchall()
        self.assertEqual(
            current[0], (record_hash(new), new.benefit.source.document_version, "published")
        )

    def test_approved_and_alias_owned_releases_are_protected(self) -> None:
        approved_value, approved_fields = self.analysis("1301", self.material("approved"))
        alias_value, alias_fields = self.analysis("1302", self.material("alias"))
        self.put_source(
            "1301", approved_value.collection_sources[0].material_sha256, approved_fields
        )
        self.put_source("1302", alias_value.collection_sources[0].material_sha256, alias_fields)
        approved = self.catalog_record(
            approved_value, self.summary(approved_value, "approved.json")
        )
        alias_target = self.catalog_record(alias_value, self.summary(alias_value, "alias.json"))
        alias_data = alias_target.model_dump(mode="json")
        alias_data["benefit"]["id"] = "youth:1302"
        alias = CatalogRecord.model_validate(alias_data)
        reviewed_at = datetime.now(timezone.utc)
        for record, text in (
            (approved, approved_value.documents[0].text),
            (alias, alias_value.documents[0].text),
        ):
            publish_policy(
                self.url,
                record,
                source_text=text,
                approved_sha256=record_hash(record),
                reviewer="fictional-integration-reviewer",
                reviewed_at=reviewed_at,
            )
        before = {record.benefit.id: record_hash(record) for record in (approved, alias)}

        result = publish_model_catalog_updates(
            self.url,
            [
                (approved, approved_value.documents[0].text, None),
                (alias_target, alias_value.documents[0].text, None),
            ],
        )
        self.assertEqual(
            result,
            {"inserted": 0, "replaced": 0, "replayed": 0, "protected_existing": 2},
        )
        with psycopg.connect(self.url) as connection:
            rows = connection.execute(
                """SELECT policy_id,approved_sha256,review_status,publication_status
                   FROM public.policy_catalog_releases ORDER BY policy_id"""
            ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual({row[0]: row[1] for row in rows}, before)
        self.assertTrue(all(row[2:] == ("approved", "published") for row in rows))


if __name__ == "__main__":
    unittest.main()
