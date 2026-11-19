"""Official-primary publication on a UUID PostgreSQL database with fictional data."""

from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import unittest
from uuid import uuid4

import psycopg

from backend.integration.postgres_fixture import test_database_url
from backend.integration.test_policy_source_rechecks_postgres import AT, dedicated_database
from backend.tests.catalog_fixtures import SOURCE_TEXT, release_data
from backend.tests.test_ai_catalog_review import (
    OFFICIAL_API_ID,
    OFFICIAL_DOCUMENT_ID,
    official_publication_fixture,
)
from benefitfinder.application.collected_policies import CollectedPolicyBatch
from benefitfinder.application.collection import CollectedRecord
from benefitfinder.infrastructure.ai_catalog_review import AIReview, publish_ai_batch
from benefitfinder.infrastructure.database.repositories.collected_policies import (
    PostgresCollectedPolicies,
)
from benefitfinder.infrastructure.database.repositories.published_policies import (
    PostgresPublishedPolicies,
    publish_policy,
)
from benefitfinder.infrastructure.policy_catalog_records import CatalogRecord, record_hash

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations/versions"


class DeciderOfficialPublicationPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database = dedicated_database(test_database_url())
        cls.url = cls.database.__enter__()
        with psycopg.connect(cls.url) as connection:
            for path in sorted(MIGRATIONS.glob("*.sql")):
                if "0007" <= path.name[:4] <= "0015":
                    connection.execute(path.read_text())

    @classmethod
    def tearDownClass(cls) -> None:
        cls.database.__exit__(None, None, None)

    def setUp(self) -> None:
        with psycopg.connect(self.url) as connection:
            connection.execute(
                "TRUNCATE public.collected_policy_runs, public.collected_policies, "
                "public.policy_catalog_releases, public.f13_clauses, "
                "public.f13_policy_documents CASCADE"
            )
        self.store = PostgresCollectedPolicies(self.url)
        self.reader = PostgresPublishedPolicies(self.url)

    def save_sources(self, rows: dict) -> None:
        for source in sorted({key[0] for key in rows}):
            records = tuple(
                CollectedRecord(source, external_id, material, "fictional-snapshot/" + external_id,
                                AT, fields)
                for (name, external_id), (fields, material, _) in rows.items()
                if name == source
            )
            self.store.save(CollectedPolicyBatch(source, uuid4().hex, AT + timedelta(seconds=1),
                                                records))

    def entry(self, record: CatalogRecord, text: str) -> tuple:
        review = AIReview(
            writer_agent="fictional-writer", reviewer_agent="fictional-reviewer",
            reviewer_model="fictional-model", verdict="pass",
            reviewed_at=AT + timedelta(seconds=2),
            document_version=record.benefit.source.document_version,
            source_sha256=record.source_sha256, record_sha256=record_hash(record),
        )
        return record, review, text, None

    def with_official_fields(self, record: CatalogRecord, rows: dict, changes: dict) -> tuple:
        """Keep the fictional binding/version current while changing stored metadata."""
        changed_rows = deepcopy(rows)
        key = ("official_documents", OFFICIAL_DOCUMENT_ID)
        fields = changed_rows[key][0] | changes
        material = hashlib.sha256(json.dumps(
            fields, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()
        changed_rows[key] = (fields, material, None)
        data = record.model_dump(mode="json")
        version = "official-v1:" + material
        data["benefit"]["source"]["document_version"] = version
        for citation in data["payment_evidence"] + [
            item for condition in data["benefit"]["eligibility_conditions"]
            for item in condition["evidence"]
        ]:
            citation["source"]["document_version"] = version
        for binding in data["collection_sources"]:
            if binding["source"] == "official_documents":
                binding["material_sha256"] = material
        return CatalogRecord.model_validate(data), changed_rows

    def test_official_only_publishes_exact_primary_and_replays(self) -> None:
        record, text, rows = official_publication_fixture(policy_id="youth:official:fictional")
        self.save_sources(rows)
        entry = self.entry(record, text)
        self.assertEqual(publish_ai_batch(self.url, [entry]),
                         {"published": 1, "replayed": 0, "replaced": 0})
        published = self.reader.get_published(record.benefit.id)
        self.assertIsNotNone(published)
        self.assertEqual(published.detail["source"]["url"], record.benefit.source.url)
        self.assertEqual(publish_ai_batch(self.url, [entry]),
                         {"published": 0, "replayed": 1, "replaced": 0})

    def test_official_primary_with_api_binding_in_either_order(self) -> None:
        for api_first in (False, True):
            with self.subTest(api_first=api_first):
                record, text, rows = official_publication_fixture(
                    include_api=True, api_first=api_first,
                )
                if not api_first:
                    self.save_sources(rows)
                result = publish_ai_batch(self.url, [self.entry(record, text)])
                self.assertEqual(result, {"published": 1, "replayed": 0, "replaced": 0})
                # Use a clean catalog while retaining the same saved sources for the second order.
                with psycopg.connect(self.url) as connection:
                    connection.execute("TRUNCATE public.policy_catalog_releases CASCADE")

    def test_official_rule_revision_preserves_history_and_replays(self) -> None:
        record, text, rows = official_publication_fixture()
        self.save_sources(rows)
        publish_ai_batch(self.url, [self.entry(record, text)])
        data = record.model_dump(mode="json")
        version = data["benefit"]["source"]["document_version"] + ":cash-rules-v1"

        def revise(value):
            if isinstance(value, dict):
                if "document_version" in value:
                    value["document_version"] = version
                for child in value.values():
                    revise(child)
            elif isinstance(value, list):
                for child in value:
                    revise(child)

        revise(data)
        data["benefit"]["conflicts"] = [{
            "policy_id": "youth:fictional-other-policy",
            "evidence": [data["payment_evidence"][0]],
        }]
        changed = CatalogRecord.model_validate(data)
        kwargs = {"allow_replace_ai": True,
                  "expected_previous": {record.benefit.id: record_hash(record)}}
        self.assertEqual(publish_ai_batch(self.url, [self.entry(changed, text)], **kwargs),
                         {"published": 0, "replayed": 0, "replaced": 1})
        self.assertEqual(publish_ai_batch(self.url, [self.entry(changed, text)], **kwargs),
                         {"published": 0, "replayed": 1, "replaced": 0})
        with psycopg.connect(self.url) as connection:
            self.assertEqual(connection.execute(
                "SELECT publication_status,count(*) FROM public.policy_catalog_releases "
                "GROUP BY publication_status ORDER BY publication_status"
            ).fetchall(), [("published", 1), ("retired", 1)])

    def test_each_absent_source_binding_blocks_publication(self) -> None:
        record, text, rows = official_publication_fixture(include_api=True)
        for missing_source in ("official_documents", "youthcenter"):
            with self.subTest(missing_source=missing_source):
                self.setUp()
                self.save_sources({key: row for key, row in rows.items()
                                   if key[0] != missing_source})
                with self.assertRaisesRegex(ValueError, "ai_catalog_source_stale"):
                    publish_ai_batch(self.url, [self.entry(record, text)])
                self.assertEqual(self.reader.list_published(), ())

    def test_wrong_policy_document_or_insufficient_category_is_rejected(self) -> None:
        record, text, rows = official_publication_fixture(include_api=True)
        for changes in (
            {"policy_id": "youth:another-policy"}, {"categories": []},
            {"categories": ["employment"]}, {"text_sha256": "0" * 64},
            {"text_status": "requires_review"},
        ):
            with self.subTest(changes=changes):
                self.setUp()
                changed, changed_rows = self.with_official_fields(record, rows, changes)
                self.save_sources(changed_rows)
                with self.assertRaisesRegex(ValueError, "ai_catalog_source_projection_mismatch"):
                    publish_ai_batch(self.url, [self.entry(changed, text)])
                self.assertEqual(self.reader.list_published(), ())

    def test_changed_or_missing_current_material_blocks_each_binding(self) -> None:
        record, text, rows = official_publication_fixture(include_api=True)
        for key in rows:
            for state in ("changed", "missing"):
                with self.subTest(source=key[0], state=state):
                    self.setUp()
                    self.save_sources(rows)
                    fields = rows[key][0] | {"fictional_change": "new-generation"}
                    material = hashlib.sha256(json.dumps(
                        fields, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                    ).encode()).hexdigest()
                    records = () if state == "missing" else (CollectedRecord(
                        key[0], key[1], material, "fictional-snapshot/new", AT, fields,
                    ),)
                    self.store.save(CollectedPolicyBatch(
                        key[0], uuid4().hex, AT + timedelta(seconds=3), records,
                    ))
                    with self.assertRaisesRegex(ValueError, "ai_catalog_source_stale"):
                        publish_ai_batch(self.url, [self.entry(record, text)])
                    self.assertEqual(self.reader.list_published(), ())

    def test_forged_primary_rolls_back_an_earlier_batch_insert(self) -> None:
        record, text, rows = official_publication_fixture(include_api=True)
        self.save_sources(rows)
        fictional = CatalogRecord.model_validate(release_data("rollback"))
        forged = text + "\n[가상] 원문에 없는 조건"
        changed = record.model_copy(update={
            "source_sha256": hashlib.sha256(forged.encode()).hexdigest(),
        })
        with self.assertRaisesRegex(ValueError, "ai_catalog_source_projection_mismatch"):
            publish_ai_batch(self.url, [self.entry(fictional, SOURCE_TEXT),
                                       self.entry(changed, forged)])
        self.assertEqual(self.reader.list_published(), ())

    def test_optional_api_still_blocks_a_conflicting_policy_alias(self) -> None:
        record, text, rows = official_publication_fixture(
            include_api=True, policy_id="youth:youthcenter:" + OFFICIAL_API_ID,
        )
        self.save_sources(rows)
        data = release_data("existing-alias")
        data["benefit"]["id"] = "youth:" + OFFICIAL_API_ID
        human = CatalogRecord.model_validate(data)
        publish_policy(self.url, human, source_text=SOURCE_TEXT, approved_sha256=record_hash(human),
                       reviewer="fictional-human", reviewed_at=AT)
        with self.assertRaisesRegex(ValueError, "ai_catalog_policy_alias_conflict"):
            publish_ai_batch(self.url, [self.entry(record, text)])
        self.assertEqual([item.policy_id for item in self.reader.list_published()],
                         [human.benefit.id])

    def test_official_path_preserves_human_release_with_both_replacement_options(self) -> None:
        record, text, rows = official_publication_fixture(include_api=True)
        self.save_sources(rows)
        publish_policy(self.url, record, source_text=text, approved_sha256=record_hash(record),
                       reviewer="fictional-human", reviewed_at=AT + timedelta(seconds=2))
        with self.assertRaisesRegex(ValueError, "ai_catalog_existing_release"):
            publish_ai_batch(self.url, [self.entry(record, text)], allow_replace_ai=True,
                             allow_replace_unreviewed=True,
                             expected_previous={record.benefit.id: record_hash(record)})
        with psycopg.connect(self.url) as connection:
            self.assertEqual(connection.execute(
                "SELECT review_type,publication_status FROM public.policy_catalog_releases"
            ).fetchall(), [("human", "published")])


if __name__ == "__main__":
    unittest.main()
