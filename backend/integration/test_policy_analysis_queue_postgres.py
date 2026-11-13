"""Real PostgreSQL leases, source invalidation and an unprivileged F12 worker."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import replace
from datetime import timedelta
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from backend.integration.test_policy_source_rechecks_postgres import (
    AT, batch, dedicated_database, publish, record,
)
from backend.tests.test_policy_analysis_pipeline import analysis_input
from backend.tests.test_policy_analysis_agent import ScriptedChat, reply, call
from benefitfinder.application.policy_analysis import PolicyAnalysisStoreError
from benefitfinder.infrastructure.database.repositories.collected_policies import PostgresCollectedPolicies
from benefitfinder.infrastructure.database.repositories.policy_analysis import PostgresPolicyAnalysisTasks
from benefitfinder.infrastructure.llm_settings import LLMSettings
from benefitfinder.infrastructure.policy_rule_files import load_f12_review_pair
from benefitfinder.jobs.__main__ import main

MIGRATION = Path(__file__).resolve().parents[1] / 'migrations/versions/0007_f12_analysis_tasks.sql'


class PolicyAnalysisQueuePostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = os.environ.get('F13_TEST_DATABASE_URL', '')
        if not cls.base or not conninfo_to_dict(cls.base).get('dbname', '').endswith('_test'):
            raise ValueError('dedicated_test_database_required')
        cls.database = dedicated_database(cls.base)
        cls.url = cls.database.__enter__()
        with psycopg.connect(cls.url) as connection:
            connection.execute(MIGRATION.read_text())
            connection.execute((MIGRATION.parent / '0011_f12_analysis_versions.sql').read_text())

    @classmethod
    def tearDownClass(cls):
        cls.database.__exit__(None, None, None)

    def setUp(self):
        with psycopg.connect(self.url) as connection:
            connection.execute('''TRUNCATE public.f12_analysis_sources,public.f12_analysis_tasks,
                public.policy_recheck_queue,public.policy_catalog_source_links,public.policy_catalog_releases,
                public.collected_policy_changes,public.collected_policies,public.collected_policy_runs,
                public.f13_clauses,public.f13_policy_documents RESTART IDENTITY''')
        self.collected = PostgresCollectedPolicies(self.url)
        self.collected.save(batch({'A': 'fictional v1'}, AT))
        self.value = analysis_input(material=hashlib.sha256(b'fictional v1').hexdigest(),
                                    at=AT + timedelta(seconds=1))
        self.tasks = PostgresPolicyAnalysisTasks(self.url)

    def complete(self, claim):
        return self.tasks.complete(claim, output_ref=f'f12-analysis/{claim.task_id}/{claim.token}',
                                   output_sha256='f' * 64, summary={'rules': 0})

    def test_registration_idempotent_completed_job_not_claimed_again(self):
        registered = self.tasks.register(self.value)
        self.assertFalse(registered['replayed'])
        self.assertEqual(self.tasks.register(self.value)['task_id'], registered['task_id'])
        claim = self.tasks.claim()
        self.assertTrue(self.tasks.is_current(claim))
        self.assertEqual(self.complete(claim), 'needs_review')
        self.assertEqual(self.complete(claim), 'needs_review')
        self.assertIsNone(self.tasks.claim())
        self.assertTrue(self.tasks.register(self.value)['replayed'])
        metadata = self.tasks.get_task(claim.task_id)
        self.assertTrue(metadata['source_current'])
        self.assertNotIn('input_payload', metadata)
        self.assertNotIn('approved', str(metadata))

    def test_freshness_and_unknown_source_require_preparation_before_claim(self):
        with self.assertRaisesRegex(PolicyAnalysisStoreError, 'f12_documents_require_refresh'):
            self.tasks.register(replace(self.value, documents=(replace(self.value.documents[0], collected_at=AT-timedelta(seconds=1)),)))
        with self.assertRaisesRegex(PolicyAnalysisStoreError, 'f12_analysis_source_stale'):
            self.tasks.register(replace(self.value, collection_sources=(replace(self.value.collection_sources[0], material_sha256='b'*64),)))
        self.assertEqual(self.tasks.status(), {'worker_analysis_version': 2, 'tasks': {}, 'source_records_awaiting_scope_or_documents': 1})
        self.assertIsNone(self.tasks.claim())

    def test_two_workers_do_not_claim_the_same_lease(self):
        self.tasks.register(self.value)
        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(lambda _: self.tasks.claim(), range(2)))
        self.assertEqual(sum(claim is not None for claim in claims), 1)

    def test_expired_lease_new_token_blocks_old_completion(self):
        self.tasks.register(self.value)
        first = self.tasks.claim()
        with psycopg.connect(self.url) as connection:
            connection.execute("UPDATE public.f12_analysis_tasks SET lease_until=clock_timestamp()-interval '1 second'")
        second = self.tasks.claim()
        self.assertEqual(second.attempt, 2)
        self.assertNotEqual(first.token, second.token)
        self.assertFalse(self.tasks.is_current(first))
        with self.assertRaisesRegex(PolicyAnalysisStoreError, 'f12_analysis_claim_lost'):
            self.complete(first)
        self.assertEqual(self.complete(second), 'needs_review')

    def test_three_failures_terminal_with_one_minute_backoff(self):
        registered = self.tasks.register(self.value)
        for attempt in range(1, 4):
            claim = self.tasks.claim()
            self.assertEqual(claim.attempt, attempt)
            status = self.tasks.fail(claim, 'PRIVATE raw error')
            self.assertEqual(status, 'failed' if attempt == 3 else 'pending')
            self.assertIsNone(self.tasks.claim())
            with psycopg.connect(self.url) as connection:
                connection.execute("UPDATE public.f12_analysis_tasks SET next_attempt_at=clock_timestamp()-interval '1 second'")
        self.assertIsNone(self.tasks.claim())
        self.assertEqual(self.tasks.get_task(registered['task_id'])['last_error'], 'f12_analysis_failed')

    def test_final_expired_lease_becomes_failed_without_fourth_attempt(self):
        self.tasks.register(self.value)
        for _ in range(3):
            self.assertIsNotNone(self.tasks.claim())
            with psycopg.connect(self.url) as connection:
                connection.execute("UPDATE public.f12_analysis_tasks SET lease_until=clock_timestamp()-interval '1 second'")
        self.assertIsNone(self.tasks.claim())
        self.assertEqual(self.tasks.get_task(1)['last_error'], 'f12_lease_expired')

    def test_source_change_invalidates_running_and_finished_drafts(self):
        for completed in (False, True):
            with self.subTest(completed=completed):
                self.setUp()
                self.tasks.register(self.value)
                claim = self.tasks.claim()
                if completed:
                    self.complete(claim)
                self.collected.save(batch({'A': 'fictional changed'}, AT+timedelta(seconds=10)))
                self.assertFalse(self.tasks.is_current(claim))
                self.assertEqual(self.tasks.get_task(claim.task_id)['status'], 'stale')
                self.assertEqual(self.complete(claim), 'stale')
                self.assertIsNone(self.tasks.claim())
                self.assertEqual(self.tasks.status()['source_records_awaiting_scope_or_documents'], 1)

    def test_unchanged_api_run_does_not_invalidate_or_duplicate_analysis(self):
        self.tasks.register(self.value)
        claim = self.tasks.claim()
        self.complete(claim)
        self.collected.save(batch({'A': 'fictional v1'}, AT+timedelta(seconds=10)))
        self.assertEqual(self.tasks.get_task(claim.task_id)['status'], 'needs_review')
        self.assertTrue(self.tasks.register(self.value)['replayed'])

    def test_missing_reappearance_requires_refetched_document_and_new_task(self):
        self.tasks.register(self.value)
        self.collected.save(batch({}, AT+timedelta(seconds=10)))
        self.collected.save(batch({'A': 'fictional v1'}, AT+timedelta(seconds=20)))
        self.assertEqual(self.tasks.get_task(1)['status'], 'stale')
        with self.assertRaisesRegex(PolicyAnalysisStoreError, 'f12_documents_require_refresh'):
            self.tasks.register(self.value)
        fresh = replace(self.value, documents=(replace(self.value.documents[0], collected_at=AT+timedelta(seconds=21)),))
        self.assertEqual(self.tasks.register(fresh)['task_id'], 2)

    def test_new_official_document_supersedes_running_old_input(self):
        self.tasks.register(self.value)
        first = self.tasks.claim()
        fresh = replace(self.value, documents=(replace(self.value.documents[0], text='가상 변경 문서', document_version='fictional-v2'),))
        self.tasks.register(fresh)
        self.assertFalse(self.tasks.is_current(first))
        self.assertEqual(self.complete(first), 'stale')
        self.assertEqual(self.tasks.claim().input, fresh)

    def test_corrupt_db_payload_fails_closed_and_commits_fixed_failure(self):
        self.tasks.register(self.value)
        with psycopg.connect(self.url) as connection:
            connection.execute("UPDATE public.f12_analysis_tasks SET input_payload='{}'::jsonb")
        with self.assertRaisesRegex(PolicyAnalysisStoreError, 'f12_invalid_input'):
            self.tasks.claim()
        self.assertEqual(self.tasks.get_task(1)['status'], 'failed')
        self.assertIsNone(self.tasks.claim())

    def test_recheck_link_cannot_reference_another_policy(self):
        publish(self.url, record('other', {}))
        with psycopg.connect(self.url) as connection:
            identifier = connection.execute("""INSERT INTO public.policy_recheck_queue
                (policy_id,document_version,reason,detected_at,approved_sha256) VALUES
                ('youth:catalog:other','fictional-v1','source_unlinked',%s,%s) RETURNING task_id""", (AT,'a'*64)).fetchone()[0]
        with self.assertRaisesRegex(PolicyAnalysisStoreError, 'f12_recheck_mismatch'):
            self.tasks.register(self.value, origin_recheck_id=identifier)
        self.assertEqual(self.tasks.status()['tasks'], {})

    def test_worker_role_can_queue_but_cannot_publish_policy_or_ddl(self):
        role = 'bf_f12_' + uuid4().hex
        with psycopg.connect(self.url) as connection:
            connection.execute(sql.SQL('CREATE ROLE {}').format(sql.Identifier(role)))
            connection.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(role)))
            connection.execute(sql.SQL('GRANT SELECT ON public.collected_policies,public.collected_policy_runs,public.collected_policy_changes,public.policy_recheck_queue TO {}').format(sql.Identifier(role)))
            connection.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE ON public.f12_analysis_tasks TO {}').format(sql.Identifier(role)))
            connection.execute(sql.SQL('GRANT SELECT,INSERT ON public.f12_analysis_sources TO {}').format(sql.Identifier(role)))
            connection.execute(sql.SQL('GRANT USAGE,SELECT ON SEQUENCE public.f12_analysis_tasks_task_id_seq TO {}').format(sql.Identifier(role)))
        def connect():
            connection = psycopg.connect(self.url, row_factory=dict_row)
            connection.execute(sql.SQL('SET ROLE {}').format(sql.Identifier(role)))
            connection.commit()
            return connection
        try:
            with patch.object(self.tasks, '_connect', side_effect=connect):
                self.tasks.register(self.value)
                claim = self.tasks.claim()
                self.assertEqual(self.complete(claim), 'needs_review')
                self.assertEqual(self.tasks.status()['tasks']['needs_review'], 1)
            with self.assertRaises(psycopg.errors.InsufficientPrivilege), connect() as connection:
                connection.execute('DELETE FROM public.policy_catalog_releases')
            with self.assertRaises(psycopg.errors.InsufficientPrivilege), connect() as connection:
                connection.execute('CREATE TABLE public.forbidden_f12 (id integer)')
        finally:
            with psycopg.connect(self.url) as connection:
                connection.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
                connection.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))

    def test_cli_with_scripted_model_writes_readable_pending_review_pair(self):
        scripted = ScriptedChat([
            reply(call('fetch_document', doc_id='fictional-document', start_line=1, end_line=1)),
            reply(call('extract_rule', doc_id='fictional-document', kind='eligibility', start_line=1, end_line=1)),
            reply(call('check_missing')), reply(call('save_candidate')),
        ])
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / 'input.json'
            path.write_text(json.dumps({'format_version': 1, **self.value.payload()}))
            output = StringIO()
            with patch.dict(os.environ, {'F12_WRITE_DATABASE_URL': self.url}), \
                 patch('benefitfinder.jobs.__main__.LLMSettings.from_env', return_value=LLMSettings('llama_cpp', 'http://127.0.0.1:18080/v1', 'fictional-test')), \
                 patch('benefitfinder.jobs.__main__.CompatibleChat', return_value=lambda m,t,**kwargs: scripted(m,t)), \
                 redirect_stdout(output), redirect_stderr(output):
                self.assertEqual(main(['f12-analysis','register','--input',str(path)]), 0)
                self.assertEqual(main(['f12-analysis','run','--data-dir',str(root)]), 0)
            task = self.tasks.get_task(1)
            self.assertEqual(task['status'], 'needs_review')
            pair = root / task['output_ref'] / 'd01'
            snapshot, facts = load_f12_review_pair(pair)
            self.assertEqual(facts.document, self.value.documents[0])
            self.assertEqual(facts.eligibility[0].quote, self.value.documents[0].text)
            self.assertEqual(snapshot.reviews, ())
            self.assertNotIn('가상 정책', output.getvalue())


    def test_analyzer_upgrade_supersedes_inflight_work_without_changing_source_hash(self):
        old = PostgresPolicyAnalysisTasks(self.url, analysis_version=1)
        registered = old.register(self.value)
        old_claim = old.claim()
        new = self.tasks.register(self.value)
        self.assertNotEqual(new['task_id'], registered['task_id'])
        previous = old.get_task(old_claim.task_id)
        self.assertEqual(previous['analysis_version'], 1)
        self.assertEqual(previous['status'], 'stale')
        self.assertEqual(previous['last_error'], 'f12_analysis_version_changed')
        self.assertEqual(previous['input_sha256'], self.value.fingerprint())
        self.assertFalse(old.is_current(old_claim))
        self.assertEqual(old.complete(old_claim,
            output_ref=f'f12-analysis/{old_claim.task_id}/{old_claim.token}',
            output_sha256='f' * 64, summary={'rules': 0}), 'stale')
        with self.assertRaisesRegex(PolicyAnalysisStoreError, 'f12_analysis_version_obsolete'):
            old.register(self.value)
        current = self.tasks.claim()
        self.assertEqual(current.analysis_version, 2)
        self.assertEqual(current.attempt, 1)
        self.assertEqual(self.complete(current), 'needs_review')
        self.assertTrue(self.tasks.register(self.value)['replayed'])
        self.assertIsNone(self.tasks.claim())

    def test_analyzer_upgrade_preserves_finished_output_and_retry_history(self):
        old = PostgresPolicyAnalysisTasks(self.url, analysis_version=1)
        old.register(self.value)
        item = old.claim()
        reference = f'f12-analysis/{item.task_id}/{item.token}'
        old.complete(item, output_ref=reference, output_sha256='c' * 64, summary={'rules': 0})
        self.tasks.register(self.value)
        previous = self.tasks.get_task(item.task_id)
        self.assertEqual(previous['status'], 'stale')
        self.assertEqual(previous['output_ref'], reference)
        self.assertEqual(previous['output_sha256'], 'c' * 64)
        self.assertEqual(previous['attempts'], 1)
        self.assertEqual(previous['input_sha256'], self.value.fingerprint())
        self.assertEqual(self.tasks.status()['tasks'], {'stale': 1, 'pending': 1})

    def test_worker_cannot_claim_or_complete_another_analysis_version(self):
        old = PostgresPolicyAnalysisTasks(self.url, analysis_version=1)
        old.register(self.value)
        self.assertIsNone(self.tasks.claim())
        item = old.claim()
        self.assertFalse(self.tasks.is_current(item))
        with self.assertRaisesRegex(PolicyAnalysisStoreError, 'f12_analysis_claim_lost'):
            self.complete(item)
        self.assertEqual(self.tasks.fail(item), 'claim_lost')
        self.assertTrue(old.is_current(item))
        self.assertEqual(self.tasks.status()['source_records_awaiting_scope_or_documents'], 1)

    def test_concurrent_version_registration_has_one_fresh_task(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            registrations = list(pool.map(lambda _: self.tasks.register(self.value), range(2)))
        self.assertEqual(len({r['task_id'] for r in registrations}), 1)
        self.assertEqual(sum(not r['replayed'] for r in registrations), 1)
        self.assertEqual(self.tasks.status()['tasks'], {'pending': 1})


    def test_migration_labels_legacy_output_without_rewriting_or_republishing(self):
        with dedicated_database(self.base) as url:
            with psycopg.connect(url) as connection:
                connection.execute(MIGRATION.read_text())
            PostgresCollectedPolicies(url).save(batch({'A': 'fictional v1'}, AT))
            with psycopg.connect(url) as connection:
                item = connection.execute("""INSERT INTO public.f12_analysis_tasks
                    (policy_id,input_sha256,input_payload,status,attempts,
                     output_ref,output_sha256,result_summary)
                    VALUES (%s,%s,%s,'needs_review',2,%s,%s,%s) RETURNING task_id""",
                    (self.value.policy_id,self.value.fingerprint(),Jsonb(self.value.payload()),
                     'f12-analysis/1/'+'a'*32,'b'*64,Jsonb({'rules':0}))).fetchone()[0]
                connection.execute("""INSERT INTO public.f12_analysis_sources
                    (task_id,source,external_id,material_sha256) VALUES (%s,'youthcenter','A',%s)""",
                    (item,self.value.collection_sources[0].material_sha256))
                connection.execute((MIGRATION.parent/'0011_f12_analysis_versions.sql').read_text())
            current = PostgresPolicyAnalysisTasks(url)
            previous = current.get_task(item)
            self.assertEqual(previous['analysis_version'],1)
            self.assertEqual(previous['status'],'needs_review')
            self.assertEqual(previous['attempts'],2)
            self.assertEqual(previous['output_sha256'],'b'*64)
            self.assertTrue(previous['source_current'])
            self.assertIsNone(current.claim())
            fresh=current.register(self.value)
            self.assertNotEqual(fresh['task_id'],item)
            previous=current.get_task(item)
            self.assertEqual(previous['status'],'stale')
            self.assertEqual(previous['output_sha256'],'b'*64)
            self.assertEqual(previous['last_error'],'f12_analysis_version_changed')
            with psycopg.connect(url) as connection:
                count=connection.execute('SELECT count(*) FROM public.policy_catalog_releases').fetchone()[0]
                self.assertEqual(count,0)
