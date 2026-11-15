"""Official API projection through real source storage, F12 queues and CLI."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
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
from benefitfinder.application.policy_analysis import PolicyAnalysisInput, PolicyAnalysisStoreError
from benefitfinder.application.prepare_policy_analysis import prepare_analysis_inputs
from benefitfinder.infrastructure.database.repositories.analysis_sources import PostgresAnalysisSources
from benefitfinder.infrastructure.database.repositories.collected_policies import PostgresCollectedPolicies
from benefitfinder.infrastructure.database.repositories.policy_analysis import PostgresPolicyAnalysisTasks
from benefitfinder.infrastructure.policy_analysis_inputs import analysis_input_from_payload
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

    def prepare(self, raw: dict | None = None,
                capture: list[PolicyAnalysisInput] | None = None) -> dict:
        def register(value: PolicyAnalysisInput) -> dict:
            if capture is not None:
                capture.append(value)
            return self.tasks.register_prepared(value)
        return prepare_analysis_inputs(preparation_plan_from_payload(raw or plan_data()),
                                       self.sources,register,material_hash)

    def latest(self) -> tuple[int,str,PolicyAnalysisInput]:
        with psycopg.connect(self.url) as connection:
            row = connection.execute('''SELECT task_id,status,input_payload
                FROM public.f12_analysis_tasks ORDER BY task_id DESC LIMIT 1''').fetchone()
        return row[0],row[1],analysis_input_from_payload(row[2])

    @staticmethod
    def two_document_plan() -> dict:
        raw = plan_data()
        raw['policies'][0]['aliases'] = ['가상 청년 취업 B']
        raw['targets'][0]['documents'].append(
            {**raw['targets'][0]['documents'][0],'external_id':'B'})
        return raw

    @staticmethod
    def supplemented(value: PolicyAnalysisInput, count: int = 1) -> PolicyAnalysisInput:
        source = value.documents[0]
        documents = tuple(replace(source,id=f'official:supplement:{index}',
            source_url=f'https://example.test/fictional-supplement/{index}',
            document_version=f'official-v1-{index}',
            text=f'[supplement]\n가상 공식 보충 문서 {index}')
            for index in range(count))
        return replace(value,documents=value.documents+documents)

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

    def test_later_api_time_with_supplement_replays_completed_task(self) -> None:
        incoming = []
        self.assertEqual(self.prepare(capture=incoming),
                         {'targets':1,'registered':1,'replayed':0,'failed':0})
        later = incoming[0].documents[0].collected_at+timedelta(minutes=5)
        full = self.supplemented(replace(incoming[0],documents=(
            replace(incoming[0].documents[0],collected_at=later),)))
        self.assertFalse(self.tasks.register(full)['replayed'])
        claim = self.tasks.claim()
        output_ref = f'f12-analysis/{claim.task_id}/{claim.token}'
        self.tasks.complete(claim,output_ref=output_ref,output_sha256='f'*64,
                            summary={'rules':0})
        before = self.tasks.get_task(claim.task_id)
        self.assertEqual(self.prepare(),{'targets':1,'registered':0,'replayed':1,'failed':0})
        self.assertEqual(self.tasks.get_task(claim.task_id),before)
        self.assertEqual((before['status'],before['attempts'],before['output_ref']),
                         ('needs_review',1,output_ref))
        latest_id,status,stored = self.latest()
        self.assertEqual((latest_id,status),(claim.task_id,'needs_review'))
        self.assertEqual([doc.id for doc in stored.documents],
                         [incoming[0].documents[0].id,'official:supplement:0'])
        self.assertEqual(stored.documents[0].collected_at,later)
        with psycopg.connect(self.url) as connection:
            self.assertEqual(connection.execute(
                'SELECT count(*) FROM public.f12_analysis_tasks').fetchone()[0],2)

    def test_changed_source_keeps_stale_supplement_until_full_refresh(self) -> None:
        self.prepare()
        _,_,api = self.latest()
        self.tasks.register(self.supplemented(api))
        stale_id,stale_status,stale = self.latest()
        self.assertEqual(stale_status,'pending')
        self.save({'A':fields(body='가상: 이번에는 30만 원을 지원합니다.')})
        incoming = []
        self.assertEqual(self.prepare(capture=incoming)['failed'],1)
        latest_id,status,stored = self.latest()
        self.assertEqual((latest_id,status),(stale_id,'stale'))
        self.assertEqual([doc.id for doc in stored.documents],
                         [doc.id for doc in stale.documents])
        refreshed = replace(incoming[0],documents=incoming[0].documents+(
            replace(stale.documents[1],
                    collected_at=incoming[0].documents[0].collected_at),))
        self.assertFalse(self.tasks.register(refreshed)['replayed'])
        claim = self.tasks.claim()
        self.assertEqual([doc.id for doc in claim.input.documents],
                         [incoming[0].documents[0].id,'official:supplement:0'])
        self.assertIn('30만 원',claim.input.documents[0].text)

    def test_prepared_refresh_replaces_api_and_appends_new_document_in_order(self) -> None:
        self.prepare()
        _,_,api = self.latest()
        self.tasks.register(self.supplemented(api))
        self.save({'A':fields(body='가상: 변경된 30만 원 지원'),'B':fields('B')})
        incoming = []
        self.assertEqual(self.prepare(self.two_document_plan(),capture=incoming)['failed'],1)
        _,_,stale = self.latest()
        refreshed = replace(incoming[0],documents=incoming[0].documents+(
            replace(stale.documents[1],
                    collected_at=incoming[0].documents[0].collected_at),))
        self.assertFalse(self.tasks.register_prepared(refreshed)['replayed'])
        claim = self.tasks.claim()
        self.assertEqual([doc.id for doc in claim.input.documents],
                         [incoming[0].documents[0].id,'official:supplement:0',
                          incoming[0].documents[1].id])
        self.assertIn('30만 원',claim.input.documents[0].text)

    def test_missing_reappearance_needs_a_later_refreshed_supplement(self) -> None:
        self.prepare()
        _,_,api = self.latest()
        self.tasks.register(self.supplemented(api))
        stale_id,_,stale = self.latest()
        self.save({})
        self.assertEqual(self.prepare()['failed'],1)
        self.save({'A':fields()})
        incoming = []
        self.assertEqual(self.prepare(capture=incoming)['failed'],1)
        latest_id,status,stored = self.latest()
        self.assertEqual((latest_id,status),(stale_id,'stale'))
        self.assertEqual([doc.id for doc in stored.documents],
                         [doc.id for doc in stale.documents])
        refreshed = replace(incoming[0],documents=incoming[0].documents+(
            replace(stale.documents[1],
                    collected_at=incoming[0].documents[0].collected_at),))
        self.assertFalse(self.tasks.register(refreshed)['replayed'])
        claim = self.tasks.claim()
        self.assertEqual(claim.input.documents[0].collected_at,
                         incoming[0].documents[0].collected_at)
        self.assertGreater(claim.input.documents[0].collected_at,
                           stale.documents[0].collected_at)
        self.assertEqual(len(claim.input.documents),2)

    def test_manual_registration_can_explicitly_remove_supplement(self) -> None:
        self.prepare()
        _,_,api = self.latest()
        self.tasks.register(self.supplemented(api))
        _,_,full = self.latest()
        fresh = replace(full.documents[0],
                        collected_at=full.documents[0].collected_at+timedelta(seconds=1))
        self.assertFalse(self.tasks.register(replace(full,documents=(fresh,)))['replayed'])
        claim = self.tasks.claim()
        self.assertEqual([doc.id for doc in claim.input.documents],[fresh.id])

    def test_automatic_registration_rejects_omitted_source_dependency(self) -> None:
        self.save({'A':fields(body='가상: 복수 원천의 새 지원 조건'),'B':fields('B')})
        self.assertEqual(self.prepare(self.two_document_plan())['registered'],1)
        previous_id,_,previous = self.latest()
        self.assertEqual({item.external_id for item in previous.collection_sources},{'A','B'})
        self.assertEqual(self.prepare()['failed'],1)
        latest_id,_,stored = self.latest()
        self.assertEqual(latest_id,previous_id)
        self.assertEqual([doc.id for doc in stored.documents],
                         [doc.id for doc in previous.documents])

    def test_supplements_cannot_exceed_the_input_document_limit(self) -> None:
        self.prepare()
        _,_,api = self.latest()
        self.tasks.register(self.supplemented(api,3))
        previous_id,_,previous = self.latest()
        self.assertEqual(len(previous.documents),4)
        self.save({'A':fields(),'B':fields('B')})
        incoming = []
        self.assertEqual(self.prepare(self.two_document_plan(),capture=incoming)['failed'],1)
        self.assertEqual(len(incoming),1)
        with self.assertRaisesRegex(PolicyAnalysisStoreError,'f12_preparation_requires_full_input'):
            self.tasks.register_prepared(incoming[0])
        latest_id,_,stored = self.latest()
        self.assertEqual(latest_id,previous_id)
        self.assertEqual([doc.id for doc in stored.documents],
                         [doc.id for doc in previous.documents])

    def test_automatic_registration_rejects_corrupted_stored_input(self) -> None:
        incoming = []
        self.prepare(capture=incoming)
        task_id,_,_ = self.latest()
        with psycopg.connect(self.url) as connection:
            connection.execute('''UPDATE public.f12_analysis_tasks SET input_payload=
                jsonb_set(input_payload,'{documents,0,text}',to_jsonb(%s::text))
                WHERE task_id=%s''',('가상: 저장 뒤 해시와 다르게 바뀐 본문',task_id))
        before = self.tasks.get_task(task_id)
        with self.assertRaisesRegex(PolicyAnalysisStoreError,'f12_invalid_input'):
            self.tasks.register_prepared(incoming[0])
        self.assertEqual(self.tasks.get_task(task_id),before)
        self.assertEqual(self.tasks.status()['tasks'],{'pending':1})

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
                                         self.tasks.register_prepared,material_hash)
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
                PostgresAnalysisSources(url),PostgresPolicyAnalysisTasks(url).register_prepared,material_hash)
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
