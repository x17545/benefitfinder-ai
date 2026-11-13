"""Official API projection through real source storage, F12 queues and CLI."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from datetime import timedelta
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
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from backend.integration.test_policy_source_rechecks_postgres import AT, dedicated_database
from backend.tests.test_policy_preparation import fields, plan_data
from benefitfinder.application.collected_policies import CollectedPolicyBatch
from benefitfinder.application.collection import CollectedRecord
from benefitfinder.application.prepare_policy_analysis import prepare_analysis_inputs
from benefitfinder.infrastructure.database.repositories.analysis_sources import PostgresAnalysisSources
from benefitfinder.infrastructure.database.repositories.collected_policies import PostgresCollectedPolicies
from benefitfinder.infrastructure.database.repositories.policy_analysis import PostgresPolicyAnalysisTasks
from benefitfinder.infrastructure.policy_preparation_plans import preparation_plan_from_payload
from benefitfinder.ingestion.versioning import record_sha256
from benefitfinder.ingestion.youthcenter import YOUTHCENTER_POLICY_LIST, hashed_fields
from benefitfinder.jobs.__main__ import main, prepare_f12_inputs

MIGRATION = Path(__file__).resolve().parents[1]/'migrations/versions/0007_f12_analysis_tasks.sql'


def material_hash(data: dict) -> str:
    return record_sha256(hashed_fields(YOUTHCENTER_POLICY_LIST,data))


class PolicyPreparationPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        base = os.environ.get('F13_TEST_DATABASE_URL','')
        if not base or not conninfo_to_dict(base).get('dbname','').endswith('_test'):
            raise ValueError('dedicated_test_database_required')
        cls.database = dedicated_database(base)
        cls.url = cls.database.__enter__()
        with psycopg.connect(cls.url) as connection:
            connection.execute(MIGRATION.read_text())
            connection.execute((MIGRATION.parent / '0011_f12_analysis_versions.sql').read_text())

    @classmethod
    def tearDownClass(cls) -> None:
        cls.database.__exit__(None,None,None)

    def setUp(self) -> None:
        with psycopg.connect(self.url) as connection:
            connection.execute('''TRUNCATE public.f12_analysis_sources,public.f12_analysis_tasks,
                public.policy_recheck_queue,public.policy_catalog_source_links,public.policy_catalog_releases,
                public.collected_policy_changes,public.collected_policies,public.collected_policy_runs,
                public.f13_clauses,public.f13_policy_documents RESTART IDENTITY''')
        self.collected = PostgresCollectedPolicies(self.url)
        self.tasks = PostgresPolicyAnalysisTasks(self.url)
        self.sources = PostgresAnalysisSources(self.url)
        self.generation = 0
        self.save({'A':fields()})

    def save(self, records: dict[str,dict]) -> None:
        self.generation += 1
        at = AT+timedelta(seconds=self.generation)
        self.collected.save(CollectedPolicyBatch('youthcenter',uuid4().hex,at,tuple(
            CollectedRecord('youthcenter',key,material_hash(value),'fictional-snapshot/'+key,
                            at-timedelta(milliseconds=100),value) for key,value in records.items())))

    def prepare(self, raw: dict | None = None) -> dict:
        return prepare_analysis_inputs(preparation_plan_from_payload(raw or plan_data()),
                                       self.sources,self.tasks.register,material_hash)

    def test_saved_api_input_has_exact_text_and_never_publishes(self) -> None:
        self.assertEqual(self.prepare(),{'targets':1,'registered':1,'replayed':0,'failed':0})
        claim = self.tasks.claim()
        doc, = claim.input.documents
        self.assertEqual(doc.collected_at,AT+timedelta(seconds=1))
        self.assertIn(fields()['support'],doc.text)
        self.assertNotIn('PRIVATE',doc.text)
        self.assertEqual(claim.input.collection_sources[0].material_sha256,material_hash(fields()))
        with psycopg.connect(self.url) as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM public.policy_catalog_releases').fetchone()[0],0)
            self.assertEqual(connection.execute('SELECT count(*) FROM public.f13_clauses').fetchone()[0],0)

    def test_unchanged_refresh_keeps_completed_input_and_never_calls_model_again(self) -> None:
        self.prepare()
        claim = self.tasks.claim()
        self.tasks.complete(claim,output_ref=f'f12-analysis/{claim.task_id}/{claim.token}',
                            output_sha256='f'*64,summary={'rules':0})
        before = self.sources.load(('A',))['A'].accepted_at
        self.save({'A':{**fields(),'inqCnt':15}})
        self.assertEqual(self.sources.load(('A',))['A'].accepted_at,before)
        self.assertEqual(self.prepare(),{'targets':1,'registered':0,'replayed':1,'failed':0})
        self.assertIsNone(self.tasks.claim())
        self.assertEqual(self.tasks.status()['tasks'],{'needs_review':1})

    def test_changed_material_invalidates_previous_input_and_registers_new_version(self) -> None:
        self.prepare()
        before = self.tasks.claim()
        self.save({'A':fields(body='가상: 이번에는 30만 원을 지원합니다.')})
        self.assertFalse(self.tasks.is_current(before))
        self.assertEqual(self.prepare()['registered'],1)
        after = self.tasks.claim()
        self.assertNotEqual(after.input.documents[0].document_version,before.input.documents[0].document_version)
        self.assertIn('30만 원',after.input.documents[0].text)
        self.assertEqual(self.tasks.status()['tasks'],{'running':1,'stale':1})

    def test_missing_source_does_not_register_and_reappearance_requires_new_input(self) -> None:
        self.prepare()
        before = self.tasks.claim()
        self.save({})
        self.assertEqual(self.prepare()['failed'],1)
        self.assertIsNone(self.tasks.claim())
        self.save({'A':fields()})
        self.assertEqual(self.tasks.status()['tasks'],{'stale':1})
        self.assertEqual(self.prepare()['registered'],1)
        after = self.tasks.claim()
        self.assertNotEqual(before.task_id,after.task_id)
        self.assertGreater(after.input.documents[0].collected_at,before.input.documents[0].collected_at)

    def test_scope_changes_fail_one_target_while_independent_target_prepares(self) -> None:
        self.save({'A':{**fields(),'category':'fictional-education'},'B':fields('B')})
        self.assertEqual(self.prepare(plan_data('A','B')),
                         {'targets':2,'registered':1,'replayed':0,'failed':1})
        self.assertEqual(self.tasks.claim().input.policy_id,'youth:fictional:B')

    def test_actual_source_category_must_match_target_even_when_other_guards_pass(self) -> None:
        self.save({'A':{**fields(),'category':'fictional-housing'}})
        plan = plan_data()
        plan['targets'][0]['documents'][0]['scope_guards'] = {'ageScope':'fictional-youth'}
        self.assertEqual(self.prepare(plan)['failed'],1)
        self.assertEqual(self.tasks.status()['tasks'],{})
        plan['policies'][0]['categories'] = ['housing']
        self.assertEqual(self.prepare(plan)['registered'],1)

    def test_racing_source_refresh_is_rechecked_before_registration(self) -> None:
        snapshots = self.sources.load(('A',))
        self.save({'A':fields(body='가상 원문 변경')})
        class OldSnapshot:
            def load(self, _ids):
                return snapshots
        result = prepare_analysis_inputs(preparation_plan_from_payload(plan_data()),OldSnapshot(),
                                         self.tasks.register,material_hash)
        self.assertEqual(result['failed'],1)
        self.assertEqual(self.tasks.status()['tasks'],{})

    def test_concurrent_preparation_uses_one_idempotent_task(self) -> None:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: self.prepare(),range(2)))
        self.assertEqual(sum(item['registered'] for item in results),1)
        self.assertEqual(sum(item['replayed'] for item in results),1)
        self.assertEqual(self.tasks.status()['tasks'],{'pending':1})

    def test_existing_least_privilege_f12_role_can_read_and_prepare(self) -> None:
        role = 'bf_preparation_'+uuid4().hex
        with psycopg.connect(self.url) as connection:
            connection.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'fictional-test-only'").format(sql.Identifier(role)))
            for statement in (
                'GRANT USAGE ON SCHEMA public TO {}',
                'GRANT SELECT ON public.collected_policies,public.collected_policy_runs,public.collected_policy_changes,public.policy_recheck_queue TO {}',
                'GRANT SELECT,INSERT,UPDATE ON public.f12_analysis_tasks TO {}',
                'GRANT SELECT,INSERT ON public.f12_analysis_sources TO {}',
                'GRANT USAGE,SELECT ON SEQUENCE public.f12_analysis_tasks_task_id_seq TO {}',
            ):
                connection.execute(sql.SQL(statement).format(sql.Identifier(role)))
        url = make_conninfo(self.url,user=role,password='fictional-test-only')
        try:
            result = prepare_analysis_inputs(preparation_plan_from_payload(plan_data()),
                PostgresAnalysisSources(url),PostgresPolicyAnalysisTasks(url).register,material_hash)
            self.assertEqual(result['registered'],1)
            with self.assertRaises(psycopg.errors.InsufficientPrivilege), psycopg.connect(url) as connection:
                connection.execute('SELECT payload FROM public.policy_catalog_releases')
        finally:
            with psycopg.connect(self.url) as connection:
                connection.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
                connection.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))

    def test_cli_prepares_without_llm_or_api_key_and_reports_partial_failure(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary)/'plan.json'
            path.write_text(json.dumps(plan_data()))
            output = StringIO()
            with patch.dict(os.environ,{'F12_WRITE_DATABASE_URL':self.url,'LLM_PROVIDER':'disabled'},clear=True),redirect_stdout(output),redirect_stderr(output):
                self.assertEqual(main(['f12-analysis','prepare','--plan',str(path)]),0)
                self.assertEqual(prepare_f12_inputs(path)['replayed'],1)
                path.write_text(json.dumps(plan_data('A','B')))
                self.assertEqual(main(['f12-analysis','prepare','--plan',str(path)]),1)
                path.write_text('{"profile":"PRIVATE bad plan"}')
                self.assertEqual(main(['f12-analysis','prepare','--plan',str(path)]),2)
            self.assertNotIn('PRIVATE',output.getvalue())
            self.assertNotIn(fields()['support'],output.getvalue())

    def test_daily_refresh_prepares_before_consuming_with_independent_source_failure(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan = root/'plan.json'
            plan.write_text(json.dumps(plan_data()))
            called = []
            def analyze(_root, *, limit):
                called.append(self.tasks.status()['tasks'])
                return {'attempted':0,'needs_review':0,'stale':0,'failed':0,
                        'source_records_awaiting_scope_or_documents':0}
            with patch.dict(os.environ,{'F12_WRITE_DATABASE_URL':self.url,'F12_PREPARATION_PLAN':str(plan)},clear=True), \
                 patch('benefitfinder.jobs.__main__._refresh_youthcenter',return_value=1), \
                 patch('benefitfinder.jobs.__main__.refresh_ecos_rate_command',return_value=0), \
                 patch('benefitfinder.jobs.__main__.run_f12_queue',side_effect=analyze), \
                 redirect_stdout(StringIO()),redirect_stderr(StringIO()):
                self.assertEqual(main(['refresh-data','--data-dir',str(root)]),1)
            self.assertEqual(called,[{'pending':1}])
            state = json.loads((root/'processed/f08/status.json').read_text())
            self.assertEqual(state['last_attempt']['stages']['f12_preparation']['counts']['registered'],1)
            self.assertEqual(state['last_attempt']['stages']['f12']['status'],'success')
