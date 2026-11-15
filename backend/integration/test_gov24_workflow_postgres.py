"""Two official sources, pending F12 and human-approved fictional releases."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

import psycopg

from backend.integration.test_policy_source_rechecks_postgres import (
    AT, batch, clauses, dedicated_database, publish, record,
)
from backend.tests.gov24_fixtures import gov24_fields, gov24_plan
from benefitfinder.application.collected_policies import CollectedPolicyBatch
from benefitfinder.application.collection import CollectedRecord
from benefitfinder.application.prepare_policy_analysis import prepare_analysis_inputs
from benefitfinder.infrastructure.database.repositories.analysis_sources import PostgresAnalysisSources
from benefitfinder.infrastructure.database.repositories.collected_policies import PostgresCollectedPolicies
from benefitfinder.infrastructure.database.repositories.policy_analysis import PostgresPolicyAnalysisTasks
from benefitfinder.infrastructure.database.repositories.published_policies import PostgresPublishedPolicies
from benefitfinder.infrastructure.policy_catalog_records import CatalogRecord
from benefitfinder.infrastructure.policy_preparation_plans import preparation_plan_from_payload
from benefitfinder.ingestion.gov24 import analysis_fields, gov24_hashed_fields
from benefitfinder.ingestion.versioning import record_sha256
from benefitfinder.jobs.__main__ import prepare_f12_inputs

MIGRATIONS = Path(__file__).resolve().parents[1] / 'migrations/versions'


def digest(fields: dict) -> str:
    return record_sha256(gov24_hashed_fields(fields))


class Gov24WorkflowPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database = dedicated_database(os.environ['F13_TEST_DATABASE_URL'])
        cls.url = cls.database.__enter__()
        with psycopg.connect(cls.url) as connection:
            for name in ('0007_f12_analysis_tasks.sql', '0008_service_catalog.sql',
                         '0009_gov24_policy_sources.sql', '0011_f12_analysis_versions.sql'):
                connection.execute((MIGRATIONS / name).read_text())

    @classmethod
    def tearDownClass(cls) -> None:
        cls.database.__exit__(None, None, None)

    def setUp(self) -> None:
        with psycopg.connect(self.url) as connection:
            connection.execute('''TRUNCATE public.f12_analysis_sources,public.f12_analysis_tasks,
                public.policy_recheck_queue,public.policy_catalog_source_links,
                public.policy_catalog_releases,public.collected_policy_changes,public.collected_policies,
                public.collected_policy_runs,public.f13_clauses,public.f13_policy_documents
                RESTART IDENTITY''')
        self.store = PostgresCollectedPolicies(self.url)
        self.tasks = PostgresPolicyAnalysisTasks(self.url)
        self.data = gov24_fields()
        self.save(self.data)

    def save(self, fields: dict, second: int = 0):
        at = AT + timedelta(seconds=second)
        return self.store.save(CollectedPolicyBatch('gov24', uuid4().hex, at, (
            CollectedRecord('gov24', 'A', digest(fields), 'fictional-gov24-snapshot', at, fields),)))

    def prepare(self):
        return prepare_analysis_inputs(preparation_plan_from_payload(gov24_plan()),
            PostgresAnalysisSources(self.url, source='gov24'), self.tasks.register_prepared,
            digest, analysis_fields)

    def test_cli_preparation_keeps_official_supplement_and_completed_review(self) -> None:
        self.prepare()
        original = self.tasks.claim().input
        supplement = replace(original.documents[0], id='supplement:fictional-gov24-notice',
                             source_url='https://example.com/fictional-official-notice',
                             document_version='fictional-notice-v1', text='가상 공식 추가 조건')
        full_input = replace(original, documents=original.documents + (supplement,))
        task = self.tasks.register(full_input)
        claim = self.tasks.claim()
        self.tasks.complete(claim, output_ref=f'f12-analysis/{claim.task_id}/{claim.token}',
                            output_sha256='f' * 64, summary={'rules': 0})
        before = self.tasks.get_task(task['task_id'])
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / 'fictional-gov24-plan.json'
            path.write_text(json.dumps(gov24_plan()))
            with patch.dict(os.environ, {'F12_WRITE_DATABASE_URL': self.url}, clear=True):
                self.assertEqual(prepare_f12_inputs(path),
                                 {'targets': 1, 'registered': 0, 'replayed': 1, 'failed': 0})
        self.assertEqual(self.tasks.get_task(task['task_id']), before)
        self.assertIsNone(self.tasks.claim())
        self.assertEqual(self.tasks.status()['tasks'], {'needs_review': 1, 'stale': 1})

    def test_id_namespace_projection_replay_and_source_change_are_isolated(self) -> None:
        self.store.save(batch({'A': 'fictional-youthcenter'}, AT))
        sources = PostgresAnalysisSources(self.url, source='gov24').load(('A',))
        self.assertEqual(sources['A'].material_sha256, digest(self.data))
        self.assertEqual(self.prepare()['registered'], 1)
        claim = self.tasks.claim()
        self.assertIn('20만 원', claim.input.documents[0].text)
        self.assertNotIn('PRIVATE', claim.input.documents[0].text)
        self.assertEqual(claim.input.collection_sources[0].source, 'gov24')
        self.assertIn('api.odcloud.kr', claim.input.documents[0].source_url)
        self.tasks.complete(claim, output_ref=f'f12-analysis/{claim.task_id}/{claim.token}',
                            output_sha256='f' * 64, summary={'rules': 0})
        self.data['serviceList']['조회수'] += 1
        self.assertEqual(self.save(self.data, 1).unchanged, 1)
        self.assertEqual(self.prepare()['replayed'], 1)
        self.assertIsNone(self.tasks.claim())
        self.data['serviceDetail']['지원내용'] = '가상: 30만 원 지원'
        self.assertEqual(self.save(self.data, 2).changed, 1)
        self.assertEqual(self.tasks.status()['tasks'], {'stale': 1})
        self.assertEqual(self.prepare()['registered'], 1)
        self.assertIn('30만 원', self.tasks.claim().input.documents[0].text)
        with psycopg.connect(self.url) as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM public.policy_catalog_releases').fetchone()[0], 0)

    def test_either_source_revokes_shared_catalog_and_f13_without_deadlock(self) -> None:
        self.store.save(batch({'A': 'fictional-youthcenter'}, AT))
        value = record('P1', {'A': 'fictional-youthcenter'}).model_dump(mode='json')
        value['collection_sources'].append({'source': 'gov24', 'external_id': 'A',
                                             'material_sha256': digest(self.data)})
        publish(self.url, CatalogRecord.model_validate(value))
        clauses(self.url, value['benefit']['id'])
        self.assertEqual(len(PostgresPublishedPolicies(self.url).list_published()), 1)
        self.data['serviceDetail']['지원내용'] += ' 변경'
        with ThreadPoolExecutor(max_workers=2) as executor:
            gov = executor.submit(self.save, self.data, 1)
            youth = executor.submit(self.store.save, batch({'A': 'changed-youth'}, AT + timedelta(seconds=1)))
            self.assertEqual(gov.result(timeout=10).changed, 1)
            self.assertEqual(youth.result(timeout=10).changed, 1)
        self.assertEqual(PostgresPublishedPolicies(self.url).list_published(), ())
        with psycopg.connect(self.url) as connection:
            self.assertGreaterEqual(connection.execute('SELECT count(*) FROM public.policy_recheck_queue').fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM public.f13_clauses WHERE review_status='approved'").fetchone()[0], 0)
        with self.assertRaises(Exception):
            publish(self.url, CatalogRecord.model_validate(value), AT + timedelta(seconds=2))

    def test_age_change_prevents_new_analysis_and_unknown_source_is_rejected(self) -> None:
        self.prepare()
        self.data['supportConditions'].update(JA0110=60, JA0111=80)
        self.save(self.data, 1)
        self.assertEqual(self.prepare()['failed'], 1)
        self.assertEqual(self.tasks.status()['tasks'], {'stale': 1})
        with psycopg.connect(self.url) as connection:
            with self.assertRaises(psycopg.errors.CheckViolation):
                connection.execute("UPDATE public.collected_policies SET source='unknown'")


if __name__ == '__main__':
    unittest.main()
