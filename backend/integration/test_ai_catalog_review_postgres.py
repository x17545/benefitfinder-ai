"""AI and human publication checks on an isolated fictional PostgreSQL database."""

import unittest
import json
import hashlib
from uuid import uuid4
from tempfile import TemporaryDirectory
from unittest.mock import patch
from datetime import timedelta
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from fastapi.testclient import TestClient

from backend.integration.postgres_fixture import test_database_url
from backend.integration.test_policy_source_rechecks_postgres import AT, batch, dedicated_database, record
from backend.tests.catalog_fixtures import source_text, release_data
from backend.tests.service_catalog_fixtures import service_release_data, service_source_text, price_source_text
from benefitfinder.infrastructure.ai_catalog_review import AIReview, publish_ai_batch, retire_ai_batch, project_youthcenter_source
from benefitfinder.infrastructure.database.repositories.collected_policies import PostgresCollectedPolicies
from benefitfinder.infrastructure.database.repositories.published_policies import PostgresPublishedPolicies, publish_policy
from benefitfinder.infrastructure.policy_catalog_records import CatalogRecord, record_hash
from benefitfinder.infrastructure.service_catalog_records import ServiceCatalogRecord, service_record_hash
from benefitfinder.infrastructure.database.repositories.published_services import PostgresPublishedServices
from benefitfinder.infrastructure.unreviewed_catalog import publish_unreviewed
from benefitfinder.jobs.__main__ import main
from benefitfinder.bootstrap import create_app
from benefitfinder.application.collection import CollectedRecord
from benefitfinder.application.collected_policies import CollectedPolicyBatch

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations/versions"


class AICatalogPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database = dedicated_database(test_database_url())
        cls.url = cls.database.__enter__()
        with psycopg.connect(cls.url) as connection:
            for path in sorted(MIGRATIONS.glob("*.sql")):
                if "0007" <= path.name[:4] <= "0014":
                    connection.execute(path.read_text())

    @classmethod
    def tearDownClass(cls):
        cls.database.__exit__(None, None, None)

    def setUp(self):
        with psycopg.connect(self.url) as connection:
            connection.execute("TRUNCATE public.collected_policy_runs, public.collected_policies, "
                               "public.policy_catalog_releases, public.f13_clauses, public.f13_policy_documents CASCADE")
        self.store = PostgresCollectedPolicies(self.url)
        self.store.save(batch({"src-A": "original-A", "src-B": "original-B"}, AT))
        self._next_generation_at = AT + timedelta(seconds=2)
        self.a = record("A", {"src-A": "original-A"})
        self.b = record("B", {"src-B": "original-B"})
        self.reader = PostgresPublishedPolicies(self.url)

    def next_generation_at(self):
        completed_at = self._next_generation_at
        self._next_generation_at += timedelta(seconds=1)
        return completed_at

    def review(self, item, **kwargs):
        values = {"writer_agent": "fictional-writer", "reviewer_agent": "fictional-reviewer",
                  "reviewer_model": "fictional-model", "verdict": "pass", "reviewed_at": AT,
                  "document_version": item.benefit.source.document_version,
                  "source_sha256": item.source_sha256, "record_sha256": record_hash(item)}
        return AIReview.model_validate(values | kwargs)

    def entry(self, item, **kwargs):
        return (item, self.review(item, **kwargs), source_text(), None)

    def published_version(self, policy_id):
        with psycopg.connect(self.url) as connection:
            return connection.execute(
                "SELECT document_version FROM public.policy_catalog_releases "
                "WHERE policy_id=%s AND publication_status='published'",
                (policy_id,),
            ).fetchone()[0]

    def real_policy(self, external_id, policy_id, key="C", version_suffix=""):
        fields = {"plcyNo": external_id, "plcyNm": f"[가상] 정책 {key}", "plcySprtCn": source_text()}
        material = hashlib.sha256(f"fictional-original-{key}".encode()).hexdigest()
        item = CollectedRecord(
            "youthcenter", external_id, material, f"fictional-snapshot/{key}", AT, fields
        )
        self.store.save(
            CollectedPolicyBatch("youthcenter", uuid4().hex, self.next_generation_at(), (item,))
        )
        official = project_youthcenter_source(fields)
        data = release_data(key)
        data["benefit"]["id"] = policy_id
        data["benefit"]["virtual_data"] = False
        data["collection_sources"] = [{
            "source": "youthcenter",
            "external_id": external_id,
            "material_sha256": material,
        }]
        data["source_sha256"] = hashlib.sha256(official.encode()).hexdigest()
        old = data["benefit"]["source"]
        new = {
            "url": "https://www.youthcenter.go.kr/youthPolicy/ythPlcyTotalSearch/ythPlcyDetail/"
            + external_id,
            "document_version": "sha256:" + material + version_suffix,
            "checked_at": old["checked_at"],
        }
        data = json.loads(json.dumps(data).replace(json.dumps(old), json.dumps(new)))
        return official, CatalogRecord.model_validate(data)

    def unreviewed_policies(self, *external_ids):
        collected = []
        policies = []
        for external_id in external_ids:
            fields = {
                "plcyNo": external_id,
                "plcyNm": "[가상] 미검수 준비 큐 정책",
                "plcySprtCn": source_text(),
                "aplyPrdSeCd": "0057002",
                "plcyAplyMthdCn": "가상 신청 방법",
            }
            material = hashlib.sha256(b"fictional-unreviewed").hexdigest()
            item = CollectedRecord(
                "youthcenter", external_id, material, "fictional-snapshot/unreviewed", AT, fields
            )
            collected.append(item)
            source = project_youthcenter_source(fields)
            data = release_data("unreviewed")
            data["benefit"].update({
                "id": "youth:youthcenter:" + external_id,
                "title": fields["plcyNm"],
                "virtual_data": False,
                "reviewed": False,
                "benefit_amount": None,
            })
            data["payments"] = None
            data["payment_evidence"] = []
            data["unresolved_combination_rules"] = True
            data["source_sha256"] = hashlib.sha256(source.encode()).hexdigest()
            data["collection_sources"] = [{
                "source": "youthcenter",
                "external_id": external_id,
                "material_sha256": material,
            }]
            old = data["benefit"]["source"]
            new = {
                "url": "https://www.youthcenter.go.kr/youthPolicy/ythPlcyTotalSearch/ythPlcyDetail/"
                + external_id,
                "document_version": "sha256:" + material,
                "checked_at": old["checked_at"],
            }
            data = json.loads(json.dumps(data).replace(json.dumps(old), json.dumps(new)))
            policies.append((CatalogRecord.model_validate(data), source))
        self.store.save(
            CollectedPolicyBatch(
                "youthcenter", uuid4().hex, self.next_generation_at(), tuple(collected)
            )
        )
        return policies

    def unreviewed_policy(self, external_id):
        return self.unreviewed_policies(external_id)[0]

    def ai_record_for(self, policy_id, version="fictional-ai-v2"):
        data = release_data("replacement")
        data["benefit"]["id"] = policy_id
        data["benefit"]["source"]["document_version"] = version
        for evidence in data["payment_evidence"] + [
            item for condition in data["benefit"]["eligibility_conditions"]
            for item in condition["evidence"]
        ]:
            evidence["source"]["document_version"] = version
        return CatalogRecord.model_validate(data)

    def test_ai_pass_source_binding_and_existing_human_path(self):
        publish_ai_batch(self.url, [self.entry(self.a)])
        publish_policy(self.url, self.b, source_text=source_text(), approved_sha256=record_hash(self.b),
                       reviewer="fictional-human", reviewed_at=AT)
        self.assertEqual(len(self.reader.list_published()), 2)
        with psycopg.connect(self.url) as connection:
            rows = connection.execute("SELECT review_type,reviewed_by FROM public.policy_catalog_releases ORDER BY policy_id").fetchall()
        self.assertEqual(rows, [("ai", "ai:fictional-reviewer"), ("human", "fictional-human")])
        self.store.save(batch({"src-A": "changed-A", "src-B": "original-B"}, AT + timedelta(days=1)))
        self.assertIsNone(self.reader.get_published(self.a.benefit.id))
        self.assertIsNotNone(self.reader.get_published(self.b.benefit.id))
        with self.assertRaisesRegex(ValueError, "ai_catalog_release_not_current"):
            publish_ai_batch(self.url, [self.entry(self.a)])

    def test_fictional_ai_release_appears_in_real_recommendation_route(self):
        publish_ai_batch(self.url, [self.entry(self.a)])
        fixture = Path(__file__).resolve().parents[2] / "contracts/fixtures/recommendation-request.json"
        request = json.loads(fixture.read_text(encoding="utf-8"))
        request["profile"]["age"] = 25
        request["profile"]["domain_data"]["interests"] = ["housing"]
        with TestClient(create_app(policy_database_url=self.url)) as client:
            response = client.post("/api/v1/recommendations", json=request)
            self.assertEqual(response.status_code, 200, response.text)
            policies = response.json()["results"]["1y"]["pv_off"]["fallback_list"]
            self.assertIn(self.a.benefit.id, [item["policy_id"] for item in policies])
            self.assertEqual(policies[0]["amount_in_period_won"], 1_000_000)
            detail = client.get("/api/v1/benefits/" + self.a.benefit.id)
            self.assertEqual(detail.status_code, 200)
            self.assertEqual(detail.json()["source"]["url"], self.a.benefit.source.url)

    def test_real_policy_source_must_equal_locked_official_fields(self):
        official, policy = self.real_policy(
            "20260101000000000001", "youth:20260101000000000001"
        )
        checked_at = AT + timedelta(seconds=3)
        review = self.review(policy, reviewed_at=checked_at)
        forged = official + "\n[가상] 존재하지 않는 지원 조건"
        forged_data = policy.model_dump(mode="json")
        forged_data["source_sha256"] = hashlib.sha256(forged.encode()).hexdigest()
        forged_policy = CatalogRecord.model_validate(forged_data)
        forged_review = self.review(forged_policy, reviewed_at=checked_at)
        with self.assertRaisesRegex(ValueError, "ai_catalog_source_projection_mismatch"):
            publish_ai_batch(self.url, [(forged_policy, forged_review, forged, None)])
        role = "bf_ai_publisher_" + uuid4().hex
        with psycopg.connect(self.url) as connection:
            connection.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'fictional-ai-test-only'").format(sql.Identifier(role)))
            connection.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
            connection.execute(sql.SQL("GRANT SELECT, INSERT ON public.policy_catalog_releases TO {}").format(sql.Identifier(role)))
            connection.execute(sql.SQL("GRANT UPDATE (publication_status) ON public.policy_catalog_releases TO {}").format(sql.Identifier(role)))
            connection.execute(sql.SQL("GRANT SELECT (version) ON public.benefitfinder_schema_migrations TO {}").format(sql.Identifier(role)))
            connection.execute(sql.SQL("GRANT SELECT (source,external_id,fields,material_sha256,missing_detected_at) ON public.collected_policies TO {}").format(sql.Identifier(role)))
        try:
            publish_ai_batch(make_conninfo(self.url, user=role, password="fictional-ai-test-only"), [(policy, review, official, None)])
            self.assertIsNotNone(self.reader.get_published(policy.benefit.id))
        finally:
            with psycopg.connect(self.url) as connection:
                connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
                connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))

    def test_preparation_policy_id_is_accepted_for_real_publication(self):
        external_id = "20260101000000000002"
        official, policy = self.real_policy(
            external_id, "youth:youthcenter:" + external_id, key="D"
        )
        review = self.review(policy, reviewed_at=AT + timedelta(seconds=3))
        self.assertEqual(
            publish_ai_batch(self.url, [(policy, review, official, None)]),
            {"published": 1, "replayed": 0, "replaced": 0},
        )
        self.assertIsNotNone(self.reader.get_published(policy.benefit.id))

    def test_real_source_accepts_decider_v3_and_rejects_forged_version_suffix(self):
        official, policy = self.real_policy(
            "20260101000000000005", "youth:20260101000000000005", key="E",
            version_suffix=":decider-v3",
        )
        review = self.review(policy, reviewed_at=AT + timedelta(seconds=3))
        self.assertEqual(
            publish_ai_batch(self.url, [(policy, review, official, None)]),
            {"published": 1, "replayed": 0, "replaced": 0},
        )

        forged_official, forged_policy = self.real_policy(
            "20260101000000000006", "youth:20260101000000000006", key="F",
            version_suffix=":decider-v4",
        )
        forged_review = self.review(forged_policy, reviewed_at=AT + timedelta(seconds=3))
        with self.assertRaisesRegex(ValueError, "ai_catalog_source_projection_mismatch"):
            publish_ai_batch(
                self.url, [(forged_policy, forged_review, forged_official, None)]
            )

    def test_replay_is_noop_and_same_version_hash_change_is_rejected(self):
        expected = {"published": 1, "replayed": 0, "replaced": 0}
        self.assertEqual(publish_ai_batch(self.url, [self.entry(self.a)]), expected)
        with psycopg.connect(self.url) as connection:
            before = connection.execute(
                "SELECT approved_sha256,published_at FROM public.policy_catalog_releases "
                "WHERE policy_id=%s AND document_version=%s",
                (self.a.benefit.id, self.a.benefit.source.document_version),
            ).fetchone()
        self.assertEqual(
            publish_ai_batch(self.url, [self.entry(self.a)]),
            {"published": 0, "replayed": 1, "replaced": 0},
        )
        changed_data = self.a.model_dump(mode="json")
        changed_data["benefit"]["summary"] += " changed"
        changed = CatalogRecord.model_validate(changed_data)
        with self.assertRaisesRegex(ValueError, "ai_catalog_version_hash_conflict"):
            publish_ai_batch(self.url, [self.entry(changed)])
        with psycopg.connect(self.url) as connection:
            rows = connection.execute(
                "SELECT approved_sha256,published_at FROM public.policy_catalog_releases"
            ).fetchall()
        self.assertEqual(rows, [before])

    def test_legacy_and_preparation_alias_cannot_duplicate_the_same_policy(self):
        external_id = '20260101000000000003'
        source, policy = self.real_policy(external_id, 'youth:' + external_id)
        entry = (policy, self.review(policy, reviewed_at=AT + timedelta(seconds=3)), source, None)
        publish_ai_batch(self.url, [entry])
        data = policy.model_dump(mode='json')
        data['benefit']['id'] = 'youth:youthcenter:' + external_id
        alias = CatalogRecord.model_validate(data)
        alias_entry = (alias, self.review(alias, reviewed_at=AT + timedelta(seconds=3)), source, None)
        with self.assertRaisesRegex(ValueError, 'ai_catalog_policy_alias_conflict'):
            publish_ai_batch(self.url, [alias_entry])
        self.assertEqual([item.policy_id for item in self.reader.list_published()], [policy.benefit.id])

    def test_duplicate_aliases_in_one_batch_roll_back_both(self):
        external_id = '20260101000000000004'
        source, policy = self.real_policy(external_id, 'youth:' + external_id)
        data = policy.model_dump(mode='json')
        data['benefit']['id'] = 'youth:youthcenter:' + external_id
        alias = CatalogRecord.model_validate(data)
        entries = [(item, self.review(item, reviewed_at=AT + timedelta(seconds=3)), source, None)
                   for item in (policy, alias)]
        with self.assertRaisesRegex(ValueError, 'ai_catalog_policy_alias_conflict'):
            publish_ai_batch(self.url, entries)
        self.assertEqual(self.reader.list_published(), ())

    def test_source_retirement_allows_new_ai_version(self):
        self.assertEqual(
            publish_ai_batch(self.url, [self.entry(self.a)]),
            {"published": 1, "replayed": 0, "replaced": 0},
        )
        self.store.save(batch({"src-A": "changed-A", "src-B": "original-B"}, AT + timedelta(days=1)))
        self.assertIsNone(self.reader.get_published(self.a.benefit.id))
        updated = record("A", {"src-A": "changed-A"}, version="fictional-v2")
        self.assertEqual(
            publish_ai_batch(
                self.url,
                [self.entry(updated, reviewed_at=AT + timedelta(days=2))],
            ),
            {"published": 1, "replayed": 0, "replaced": 0},
        )
        self.assertEqual(self.published_version(self.a.benefit.id), "fictional-v2")
        with psycopg.connect(self.url) as connection:
            rows = connection.execute(
                "SELECT document_version,publication_status FROM public.policy_catalog_releases "
                "WHERE policy_id=%s ORDER BY document_version",
                (self.a.benefit.id,),
            ).fetchall()
        self.assertEqual(rows, [("fictional-v1", "retired"), ("fictional-v2", "published")])

    def test_allow_replace_ai_uses_expected_previous_cas(self):
        publish_ai_batch(self.url, [self.entry(self.a)])
        updated = record("A", {"src-A": "original-A"}, version="fictional-v2")
        previous = record_hash(self.a)
        with self.assertRaisesRegex(ValueError, "ai_catalog_existing_release"):
            publish_ai_batch(self.url, [self.entry(updated)])
        with self.assertRaisesRegex(ValueError, "ai_catalog_replacement_requires_expected_previous"):
            publish_ai_batch(self.url, [self.entry(updated)], allow_replace_ai=True)
        with self.assertRaisesRegex(ValueError, "ai_catalog_replacement_conflict"):
            publish_ai_batch(
                self.url, [self.entry(updated)], allow_replace_ai=True,
                expected_previous={self.a.benefit.id: "0" * 64},
            )
        self.assertEqual(self.published_version(self.a.benefit.id), "fictional-v1")
        self.assertEqual(
            publish_ai_batch(
                self.url, [self.entry(updated)], allow_replace_ai=True,
                expected_previous={self.a.benefit.id: previous},
            ),
            {"published": 0, "replayed": 0, "replaced": 1},
        )
        self.assertEqual(self.published_version(self.a.benefit.id), "fictional-v2")
        self.assertEqual(
            publish_ai_batch(
                self.url, [self.entry(updated)], allow_replace_ai=True,
                expected_previous={self.a.benefit.id: previous},
            ),
            {"published": 0, "replayed": 1, "replaced": 0},
        )

    def test_replacement_failure_rolls_back_all_current_releases(self):
        publish_ai_batch(self.url, [self.entry(self.a), self.entry(self.b)])
        updated_a = record("A", {"src-A": "original-A"}, version="fictional-v2")
        updated_b = record("B", {"src-B": "original-B"}, version="fictional-v2")
        with self.assertRaisesRegex(ValueError, "ai_catalog_replacement_requires_expected_previous"):
            publish_ai_batch(
                self.url,
                [self.entry(updated_a), self.entry(updated_b)],
                allow_replace_ai=True,
                expected_previous={self.a.benefit.id: record_hash(self.a)},
            )
        with psycopg.connect(self.url) as connection:
            rows = connection.execute(
                "SELECT policy_id,document_version,publication_status "
                "FROM public.policy_catalog_releases ORDER BY policy_id"
            ).fetchall()
        self.assertEqual(
            rows,
            [
                (self.a.benefit.id, "fictional-v1", "published"),
                (self.b.benefit.id, "fictional-v1", "published"),
            ],
        )

    def test_human_release_is_never_replaced_by_ai(self):
        publish_policy(
            self.url, self.a, source_text=source_text(), approved_sha256=record_hash(self.a),
            reviewer="fictional-human", reviewed_at=AT,
        )
        updated = self.ai_record_for(self.a.benefit.id)
        with self.assertRaisesRegex(ValueError, "ai_catalog_existing_release"):
            publish_ai_batch(
                self.url, [self.entry(updated)], allow_replace_ai=True,
                allow_replace_unreviewed=True,
                expected_previous={self.a.benefit.id: record_hash(self.a)},
            )
        with psycopg.connect(self.url) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT review_type,publication_status FROM public.policy_catalog_releases"
                ).fetchall(),
                [("human", "published")],
            )

    def test_unreviewed_release_is_never_replaced_by_ai(self):
        item, source = self.unreviewed_policy("20260101000000000003")
        publish_unreviewed(self.url, [(item, source)])
        updated = self.ai_record_for(item.benefit.id)
        with self.assertRaisesRegex(ValueError, "ai_catalog_existing_release"):
            publish_ai_batch(
                self.url, [self.entry(updated)], allow_replace_ai=True,
                expected_previous={item.benefit.id: record_hash(item)},
            )
        with psycopg.connect(self.url) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT review_type,publication_status FROM public.policy_catalog_releases"
                ).fetchall(),
                [("unreviewed", "published")],
            )

    def test_allow_replace_unreviewed_uses_exact_cas_and_preserves_replay(self):
        item, source = self.unreviewed_policy("20260101000000000007")
        publish_unreviewed(self.url, [(item, source)])
        previous = record_hash(item)
        updated = self.ai_record_for(item.benefit.id)
        updated_entry = self.entry(updated, reviewed_at=AT + timedelta(seconds=3))
        with self.assertRaisesRegex(ValueError, "ai_catalog_existing_release"):
            publish_ai_batch(
                self.url, [updated_entry], allow_replace_ai=True,
                expected_previous={item.benefit.id: previous},
            )
        with self.assertRaisesRegex(ValueError, "ai_catalog_replacement_requires_expected_previous"):
            publish_ai_batch(
                self.url, [updated_entry], allow_replace_unreviewed=True
            )
        with self.assertRaisesRegex(ValueError, "ai_catalog_replacement_conflict"):
            publish_ai_batch(
                self.url, [updated_entry], allow_replace_unreviewed=True,
                expected_previous={item.benefit.id: "0" * 64},
            )
        self.assertEqual(
            publish_ai_batch(
                self.url, [updated_entry], allow_replace_unreviewed=True,
                expected_previous={item.benefit.id: previous},
            ),
            {"published": 0, "replayed": 0, "replaced": 1},
        )
        self.assertEqual(self.published_version(item.benefit.id), "fictional-ai-v2")
        with psycopg.connect(self.url) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT document_version,review_type,publication_status "
                    "FROM public.policy_catalog_releases WHERE policy_id=%s "
                    "ORDER BY document_version",
                    (item.benefit.id,),
                ).fetchall(),
                [
                    ("fictional-ai-v2", "ai", "published"),
                    (item.benefit.source.document_version, "unreviewed", "retired"),
                ],
            )
        self.assertEqual(
            publish_ai_batch(
                self.url, [updated_entry], allow_replace_unreviewed=True,
                expected_previous={item.benefit.id: previous},
            ),
            {"published": 0, "replayed": 1, "replaced": 0},
        )

    def test_unreviewed_same_version_digest_change_is_rejected(self):
        item, source = self.unreviewed_policy("20260101000000000008")
        publish_unreviewed(self.url, [(item, source)])
        same_version = self.ai_record_for(
            item.benefit.id, version=item.benefit.source.document_version
        )
        with self.assertRaisesRegex(ValueError, "ai_catalog_version_hash_conflict"):
            publish_ai_batch(
                self.url, [self.entry(same_version)], allow_replace_unreviewed=True,
                expected_previous={item.benefit.id: record_hash(item)},
            )
        with psycopg.connect(self.url) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT document_version,review_type,publication_status "
                    "FROM public.policy_catalog_releases WHERE policy_id=%s",
                    (item.benefit.id,),
                ).fetchall(),
                [(item.benefit.source.document_version, "unreviewed", "published")],
            )

    def test_unreviewed_replacement_failure_rolls_back_prior_promotion(self):
        (first, first_source), (second, second_source) = self.unreviewed_policies(
            "20260101000000000009", "20260101000000000010"
        )
        publish_unreviewed(self.url, [(first, first_source), (second, second_source)])
        updated_first = self.ai_record_for(first.benefit.id)
        updated_second = self.ai_record_for(second.benefit.id)
        first_entry = self.entry(updated_first, reviewed_at=AT + timedelta(seconds=3))
        second_entry = self.entry(updated_second, reviewed_at=AT + timedelta(seconds=3))
        with self.assertRaisesRegex(ValueError, "ai_catalog_replacement_requires_expected_previous"):
            publish_ai_batch(
                self.url,
                [first_entry, second_entry],
                allow_replace_unreviewed=True,
                expected_previous={first.benefit.id: record_hash(first)},
            )
        with psycopg.connect(self.url) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT policy_id,document_version,review_type,publication_status "
                    "FROM public.policy_catalog_releases ORDER BY policy_id"
                ).fetchall(),
                [
                    (first.benefit.id, first.benefit.source.document_version,
                     "unreviewed", "published"),
                    (second.benefit.id, second.benefit.source.document_version,
                     "unreviewed", "published"),
                ],
            )

    def test_service_source_and_price_are_verified(self):
        service = ServiceCatalogRecord.model_validate(service_release_data())
        review = self.review(self.a, document_version=service.benefit.source.document_version,
                             source_sha256=service.source_sha256, record_sha256=service_record_hash(service))
        with self.assertRaises(ValueError):
            publish_ai_batch(self.url, [(service, review, service_source_text(), price_source_text() + " changed")])
        publish_ai_batch(self.url, [(service, review, service_source_text(), price_source_text())])
        self.assertIsNotNone(PostgresPublishedServices(self.url).get_published(service.benefit.id))

    def test_reject_failed_hold_and_hash_mismatch(self):
        for args in ({"verdict": "revise"}, {"verdict": "hold"}, {"record_sha256": "0" * 64},
                     {"source_sha256": "0" * 64}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                publish_ai_batch(self.url, [self.entry(self.a, **args)])
        self.assertEqual(self.reader.list_published(), ())

    def test_failure_rolls_back_entire_batch_and_existing_release(self):
        publish_policy(self.url, self.a, source_text=source_text(), approved_sha256=record_hash(self.a),
                       reviewer="fictional-human", reviewed_at=AT)
        with self.assertRaisesRegex(ValueError, "ai_catalog_existing_release"):
            publish_ai_batch(self.url, [self.entry(self.b), self.entry(self.a)])
        self.assertIsNotNone(self.reader.get_published(self.a.benefit.id))
        self.assertIsNone(self.reader.get_published(self.b.benefit.id))
        with psycopg.connect(self.url) as connection:
            self.assertEqual(connection.execute("SELECT review_type FROM public.policy_catalog_releases").fetchone()[0], "human")

    def test_retired_release_same_version_is_rejected_and_rolls_back_batch(self):
        publish_policy(self.url, self.a, source_text=source_text(), approved_sha256=record_hash(self.a),
                       reviewer="fictional-human", reviewed_at=AT)
        with psycopg.connect(self.url) as connection:
            connection.execute("UPDATE public.policy_catalog_releases SET publication_status='retired' WHERE policy_id=%s", (self.a.benefit.id,))
        with self.assertRaisesRegex(ValueError, "ai_catalog_release_not_current"):
            publish_ai_batch(self.url, [self.entry(self.b), self.entry(self.a)])
        self.assertIsNone(self.reader.get_published(self.b.benefit.id))
        with psycopg.connect(self.url) as connection:
            self.assertEqual(connection.execute("SELECT publication_status,review_type FROM public.policy_catalog_releases").fetchall(), [("retired", "human")])

    def test_targeted_retirement_is_atomic_and_preserves_human_release(self):
        publish_ai_batch(self.url, [self.entry(self.a)])
        publish_policy(self.url, self.b, source_text=source_text(), approved_sha256=record_hash(self.b),
                       reviewer="fictional-human", reviewed_at=AT)
        correct = (self.a.benefit.id, self.a.benefit.source.document_version, record_hash(self.a))
        with self.assertRaisesRegex(ValueError, "ai_catalog_release_not_current"):
            retire_ai_batch(self.url, [correct, (self.b.benefit.id, self.b.benefit.source.document_version, record_hash(self.b))])
        self.assertIsNotNone(self.reader.get_published(self.a.benefit.id))
        retire_ai_batch(self.url, [correct])
        self.assertIsNone(self.reader.get_published(self.a.benefit.id))
        self.assertIsNotNone(self.reader.get_published(self.b.benefit.id))

    def test_cli_validates_publishes_and_retires_exact_batch(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "record.json").write_text(self.a.model_dump_json(), encoding="utf-8")
            (root / "review.json").write_text(self.review(self.a).model_dump_json(), encoding="utf-8")
            (root / "source.txt").write_text(source_text(), encoding="utf-8")
            (root / "manifest.json").write_text(json.dumps([{"input": "record.json", "review": "review.json", "source": "source.txt"}]), encoding="utf-8")
            with patch.dict("os.environ", {"POLICY_PUBLISH_DATABASE_URL": self.url}):
                self.assertEqual(main(["ai-catalog", "validate", "--manifest", str(root / "manifest.json")]), 0)
                self.assertEqual(main(["ai-catalog", "publish", "--manifest", str(root / "manifest.json")]), 0)
                self.assertIsNotNone(self.reader.get_published(self.a.benefit.id))
                self.assertEqual(main(["ai-catalog", "retire", "--manifest", str(root / "manifest.json")]), 0)
                self.assertIsNone(self.reader.get_published(self.a.benefit.id))

    def test_ai_provenance_cannot_be_mutated(self):
        publish_ai_batch(self.url, [self.entry(self.a)])
        with self.assertRaises(psycopg.errors.RaiseException):
            with psycopg.connect(self.url) as connection:
                connection.execute("UPDATE public.policy_catalog_releases SET ai_review='{}'::jsonb")


if __name__ == "__main__":
    unittest.main()
