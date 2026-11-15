"""Unreviewed publication uses fictional data in a separate PostgreSQL database."""

import hashlib
import json
import unittest
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient

from backend.integration.postgres_fixture import test_database_url
from backend.integration.test_policy_source_rechecks_postgres import AT, dedicated_database
from backend.tests.catalog_fixtures import release_data, source_text
from benefitfinder.application.collected_policies import CollectedPolicyBatch
from benefitfinder.application.collection import CollectedRecord
from benefitfinder.infrastructure.ai_catalog_review import project_youthcenter_source
from benefitfinder.infrastructure.database.repositories.collected_policies import PostgresCollectedPolicies
from benefitfinder.infrastructure.database.repositories.published_policies import PostgresPublishedPolicies, publish_policy
from benefitfinder.infrastructure.policy_catalog_records import CatalogRecord, record_hash
from benefitfinder.infrastructure.unreviewed_catalog import publish_unreviewed, retire_unreviewed
from benefitfinder.bootstrap import create_app

MIGRATIONS = Path(__file__).resolve().parents[1] / 'migrations/versions'


class UnreviewedCatalogPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database = dedicated_database(test_database_url())
        cls.url = cls.database.__enter__()
        with psycopg.connect(cls.url) as connection:
            for path in sorted(MIGRATIONS.glob('*.sql')):
                if '0007' <= path.name[:4] <= '0014':
                    connection.execute(path.read_text())

    @classmethod
    def tearDownClass(cls):
        cls.database.__exit__(None, None, None)

    def setUp(self):
        with psycopg.connect(self.url) as connection:
            connection.execute('TRUNCATE public.collected_policy_runs, public.collected_policies, '
                               'public.policy_catalog_releases, public.f13_clauses, public.f13_policy_documents CASCADE')
        self.store = PostgresCollectedPolicies(self.url)
        self.reader = PostgresPublishedPolicies(self.url)
        self.items = []

    def entry(self, suffix):
        external_id = '2026010100000000000' + str(suffix)
        data = release_data(str(suffix))
        data['benefit']['id'] = 'youth:' + external_id
        data['benefit']['virtual_data'] = False
        data['benefit']['reviewed'] = False
        data['benefit']['benefit_amount'] = None
        data['payments'] = None
        data['payment_evidence'] = []
        data['unresolved_combination_rules'] = True
        fields = {'plcyNo': external_id, 'plcyNm': data['benefit']['title'], 'plcySprtCn': source_text(),
                  'aplyPrdSeCd': '0057002', 'plcyAplyMthdCn': '가상 신청 방법'}
        material = hashlib.sha256(('fictional' + suffix).encode()).hexdigest()
        item = CollectedRecord('youthcenter', external_id, material, 'fictional/' + suffix, AT, fields)
        self.items.append(item)
        self.store.save(CollectedPolicyBatch('youthcenter', uuid4().hex, AT + timedelta(seconds=int(suffix)), tuple(self.items)))
        source = project_youthcenter_source(fields)
        data['source_sha256'] = hashlib.sha256(source.encode()).hexdigest()
        data['collection_sources'] = [{'source': 'youthcenter', 'external_id': external_id, 'material_sha256': material}]
        old = data['benefit']['source']
        new = {'url': 'https://www.youthcenter.go.kr/youthPolicy/ythPlcyTotalSearch/ythPlcyDetail/' + external_id,
               'document_version': 'sha256:' + material, 'checked_at': old['checked_at']}
        data = json.loads(json.dumps(data).replace(json.dumps(old), json.dumps(new)))
        return CatalogRecord.model_validate(data), source

    def test_unreviewed_is_readable_and_retired_without_fake_approval(self):
        entry = self.entry('1')
        publish_unreviewed(self.url, [entry])
        with psycopg.connect(self.url) as connection:
            row = connection.execute('SELECT review_type,review_status,reviewed_by,reviewed_at,ai_review FROM public.policy_catalog_releases').fetchone()
        self.assertEqual(row, ('unreviewed', 'unreviewed', None, None, None))
        self.assertIsNotNone(self.reader.get_published(entry[0].benefit.id))
        with self.assertRaises(psycopg.errors.RaiseException):
            with psycopg.connect(self.url) as connection:
                connection.execute("UPDATE public.policy_catalog_releases SET unreviewed_source='{}'::jsonb")
        retire_unreviewed(self.url, [entry])
        self.assertIsNone(self.reader.get_published(entry[0].benefit.id))
        altered = entry[0].model_dump(mode='json')
        altered['benefit']['summary'] += ' changed'
        with self.assertRaisesRegex(ValueError, 'unreviewed_retired_release_mismatch'):
            publish_unreviewed(self.url, [(CatalogRecord.model_validate(altered), entry[1])])
        publish_unreviewed(self.url, [entry])
        self.assertIsNotNone(self.reader.get_published(entry[0].benefit.id))

    def test_forged_source_and_batch_collision_roll_back(self):
        first, second = self.entry('1'), self.entry('2')
        publish_unreviewed(self.url, [first])
        with self.assertRaises(ValueError):
            publish_unreviewed(self.url, [second, first])
        self.assertIsNone(self.reader.get_published(second[0].benefit.id))
        with self.assertRaises(ValueError):
            publish_unreviewed(self.url, [(second[0], second[1] + ' forged')])

    def test_unreviewed_is_excluded_from_cash_calculation_but_visible_in_api(self):
        entry = self.entry('1')
        publish_unreviewed(self.url, [entry])
        fixture = Path(__file__).resolve().parents[2] / 'contracts/fixtures/recommendation-request.json'
        request = json.loads(fixture.read_text(encoding='utf-8'))
        request['profile']['age'] = 25
        request['profile']['domain_data']['interests'] = ['housing']
        with TestClient(create_app(policy_database_url=self.url)) as client:
            response = client.post('/api/v1/recommendations', json=request)
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()['results']['1y']['pv_off']
            self.assertIn(entry[0].benefit.id, [x['policy_id'] for x in result['not_yet_reviewed']])
            self.assertEqual(client.get('/api/v1/benefits/' + entry[0].benefit.id).status_code, 200)

    def test_source_change_retires_unreviewed_record(self):
        entry = self.entry('1')
        publish_unreviewed(self.url, [entry])
        old = self.items[0]
        changed = CollectedRecord(old.source_name, old.external_id, hashlib.sha256(b'changed').hexdigest(),
                                  old.snapshot_ref, AT, old.fields)
        self.store.save(CollectedPolicyBatch('youthcenter', uuid4().hex, AT + timedelta(days=1), (changed,)))
        self.assertIsNone(self.reader.get_published(entry[0].benefit.id))
        with self.assertRaises(ValueError):
            publish_unreviewed(self.url, [entry])
        with psycopg.connect(self.url) as connection:
            self.assertEqual(connection.execute('SELECT review_status,publication_status FROM public.policy_catalog_releases').fetchone(),
                             ('pending', 'retired'))

    def test_existing_human_approval_is_preserved(self):
        entry = self.entry('1')
        publish_policy(self.url, entry[0], source_text=entry[1], approved_sha256=record_hash(entry[0]),
                       reviewer='fictional-human', reviewed_at=AT + timedelta(minutes=1))
        with psycopg.connect(self.url) as connection:
            row = connection.execute('SELECT review_type,review_status,reviewed_by FROM public.policy_catalog_releases').fetchone()
        self.assertEqual(row, ('human', 'approved', 'fictional-human'))
        self.assertIsNotNone(self.reader.get_published(entry[0].benefit.id))
