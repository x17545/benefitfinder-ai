"""Official-document source migration and shared invalidation in PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
import hashlib
import os
from pathlib import Path
import threading
import unittest
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from backend.integration.test_policy_source_rechecks_postgres import (
    AT, batch, clauses, dedicated_database, publish,
)
from backend.tests.catalog_fixtures import release_data
from benefitfinder.application.collected_policies import CollectedPolicyBatch
from benefitfinder.application.collection import CollectedRecord, SourceResponse
from benefitfinder.application.official_documents import SOURCE_NAME, OfficialDocumentTarget
from benefitfinder.application.policy_analysis import (
    AnalysisPolicy, AnalysisSource, PolicyAnalysisInput,
)
from benefitfinder.application.policy_rules import SourceDocument
from benefitfinder.infrastructure.database.repositories.collected_policies import (
    PostgresCollectedPolicies,
)
from benefitfinder.infrastructure.database.repositories.policy_analysis import (
    PostgresPolicyAnalysisTasks,
)
from benefitfinder.infrastructure.database.repositories.published_policies import (
    PostgresPublishedPolicies,
)
from benefitfinder.infrastructure.policy_catalog_records import CatalogRecord, record_hash
from benefitfinder.ingestion.official_documents import document_fields
from benefitfinder.ingestion.versioning import record_sha256

MIGRATIONS = Path(__file__).resolve().parents[1] / 'migrations/versions'
POST_0006_MIGRATIONS = (
    '0007_f12_analysis_tasks.sql',
    '0008_service_catalog.sql',
    '0009_gov24_policy_sources.sql',
    '0010_catalog_explanation_publication.sql',
    '0011_f12_analysis_versions.sql',
)
SOURCE_MIGRATION = '0012_official_document_sources.sql'
POLICY_ID = 'youth:catalog:OFFICIAL'
OFFICIAL_ID = 'notice:fictional-seoul-001'
OFFICIAL_URL = 'https://example.test/fictional-official-notice'
API_ID = 'A'
API_VALUE = 'fictional-api-v1'
HTML_V1 = '<html><body><p>가상 공식 조건: 만 19세 이상</p></body></html>'.encode()
HTML_V2 = (
    '<html><body><p>가상 공식 조건: 만 19세 이상, 소득 기준 변경</p></body></html>'.encode()
)
TARGET = OfficialDocumentTarget(
    external_id=OFFICIAL_ID,
    policy_id=POLICY_ID,
    url=OFFICIAL_URL,
    format='html',
    categories=('housing',),
    required_text=('가상 공식 조건',),
)


def official_fields(body: bytes, *, url: str = OFFICIAL_URL) -> dict[str, object]:
    response = SourceResponse(200, body, 'text/html; charset=utf-8')
    return document_fields(replace(TARGET, url=url), response, lambda _body: ())


def api_digest() -> str:
    return hashlib.sha256(API_VALUE.encode()).hexdigest()


def catalog_record(bindings: list[dict[str, str]], *, version: str = 'fictional-v1'):
    data = release_data('OFFICIAL')
    data['benefit']['source']['document_version'] = version
    evidence = data['payment_evidence'] + [
        item
        for condition in data['benefit']['eligibility_conditions']
        for item in condition['evidence']
    ]
    for item in evidence:
        item['source']['document_version'] = version
    data['collection_sources'] = bindings
    return CatalogRecord.model_validate(data)


def f12_input(official_digest: str, *, include_api: bool = False) -> PolicyAnalysisInput:
    sources = [AnalysisSource(SOURCE_NAME, OFFICIAL_ID, official_digest)]
    if include_api:
        sources.append(AnalysisSource('youthcenter', API_ID, api_digest()))
    return PolicyAnalysisInput(
        POLICY_ID,
        (
            SourceDocument(
                OFFICIAL_ID,
                POLICY_ID,
                OFFICIAL_URL,
                'fictional-v1',
                AT + timedelta(seconds=2),
                '가상 공식 조건: 만 19세 이상입니다.',
                True,
            ),
        ),
        (AnalysisPolicy(POLICY_ID, '[가상] 정책 OFFICIAL', ('housing',)),),
        tuple(sorted(sources, key=lambda item: item.source)),
    )


class OfficialDocumentSourcesPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base = os.environ.get('F13_TEST_DATABASE_URL', '')
        if not cls.base or not conninfo_to_dict(cls.base).get('dbname', '').endswith('_test'):
            raise ValueError('dedicated_test_database_required')
        cls.database = dedicated_database(cls.base)
        cls.url = cls.database.__enter__()
        with psycopg.connect(cls.url) as connection:
            for name in (*POST_0006_MIGRATIONS, SOURCE_MIGRATION):
                connection.execute((MIGRATIONS / name).read_text())

    @classmethod
    def tearDownClass(cls) -> None:
        cls.database.__exit__(None, None, None)

    def setUp(self) -> None:
        with psycopg.connect(self.url) as connection:
            connection.execute('''TRUNCATE public.f12_analysis_sources,
                public.f12_analysis_tasks,public.policy_recheck_queue,
                public.policy_catalog_source_links,public.policy_catalog_releases,
                public.collected_policy_changes,public.collected_policies,
                public.collected_policy_runs,public.f13_clauses,
                public.f13_policy_documents RESTART IDENTITY''')
        self.collected = PostgresCollectedPolicies(self.url)
        self.tasks = PostgresPolicyAnalysisTasks(self.url)

    def save_official(self, fields: dict[str, object], at, *, store=None):
        digest = record_sha256(fields)
        record = CollectedRecord(
            SOURCE_NAME,
            OFFICIAL_ID,
            digest,
            f'official_documents/snapshots/fictional-{digest[:12]}.body',
            at - timedelta(seconds=1),
            fields,
        )
        result = (store or self.collected).save(
            CollectedPolicyBatch(SOURCE_NAME, uuid4().hex, at, (record,))
        )
        return result, digest

    def complete_f12(self, value: PolicyAnalysisInput) -> tuple[int, object, str]:
        task = self.tasks.register(value)
        claim = self.tasks.claim()
        self.assertIsNotNone(claim)
        output_ref = f'f12-analysis/{claim.task_id}/{claim.token}'
        self.assertEqual(
            self.tasks.complete(
                claim,
                output_ref=output_ref,
                output_sha256='c' * 64,
                summary={'rules': 0},
            ),
            'needs_review',
        )
        return task['task_id'], claim, output_ref

    def test_migration_extends_every_check_and_does_not_retire_existing_data(self) -> None:
        constraints = {
            'public.collected_policy_runs': 'collected_policy_runs_source_check',
            'public.collected_policies': 'collected_policies_source_check',
            'public.collected_policy_changes': 'collected_policy_changes_source_check',
            'public.policy_catalog_source_links': 'policy_catalog_source_links_source_check',
            'public.f12_analysis_sources': 'f12_analysis_sources_source_check',
        }
        function_names = (
            'guard_catalog_source_bindings',
            'lock_f13_source_writes',
            'guard_catalog_benefit_kind',
        )
        with psycopg.connect(self.url) as connection:
            definitions = dict(connection.execute(
                '''SELECT conname,pg_get_constraintdef(oid) FROM pg_constraint
                   WHERE conname=ANY(%s)''',
                (list(constraints.values()),),
            ).fetchall())
            for table, constraint in constraints.items():
                with self.subTest(table=table):
                    definition = definitions[constraint]
                    for source in ('gov24', 'official_documents', 'youthcenter'):
                        self.assertIn(source, definition)

            triggers = connection.execute(
                '''SELECT event.relname,t.tgname FROM pg_trigger t
                   JOIN pg_class event ON event.oid=t.tgrelid
                   WHERE NOT t.tgisinternal AND t.tgname=ANY(%s)
                   ORDER BY event.relname,t.tgname''',
                ([
                    'catalog_source_write_lock',
                    'f13_clause_source_lock',
                    'f13_document_source_lock',
                ],),
            ).fetchall()
            self.assertEqual(triggers, [
                ('f13_clauses', 'f13_clause_source_lock'),
                ('f13_policy_documents', 'f13_document_source_lock'),
                ('policy_catalog_releases', 'catalog_source_write_lock'),
            ])

            functions = connection.execute(
                '''SELECT p.proname,p.prosecdef,p.proconfig,p.prosrc,
                       COALESCE((SELECT bool_or(a.grantee=0
                           AND a.privilege_type='EXECUTE')
                           FROM aclexplode(p.proacl) a),false)
                   FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                   WHERE n.nspname='public' AND p.proname=ANY(%s)''',
                (list(function_names),),
            ).fetchall()
            self.assertEqual({row[0] for row in functions}, set(function_names))
            for name, security_definer, config, body, public_execute in functions:
                with self.subTest(function=name):
                    self.assertTrue(security_definer)
                    self.assertEqual(config, ['search_path=pg_catalog'])
                    self.assertFalse(public_execute)
                    for source in ('gov24', 'official_documents', 'youthcenter'):
                        self.assertIn(source, body)
                    if name != 'guard_catalog_source_bindings':
                        positions = [body.index(f'collected_policy_generations:{source}')
                                     for source in ('gov24', 'official_documents', 'youthcenter')]
                        self.assertEqual(positions, sorted(positions))
            self.assertEqual(connection.execute(
                '''SELECT 1 FROM public.benefitfinder_schema_migrations
                   WHERE version='0012_official_document_sources' '''
            ).fetchone(), (1,))

        with dedicated_database(self.base) as legacy_url:
            with psycopg.connect(legacy_url) as connection:
                for name in POST_0006_MIGRATIONS:
                    connection.execute((MIGRATIONS / name).read_text())
            PostgresCollectedPolicies(legacy_url).save(
                batch({API_ID: API_VALUE}, AT)
            )
            publish(
                legacy_url,
                catalog_record([{
                    'source': 'youthcenter',
                    'external_id': API_ID,
                    'material_sha256': api_digest(),
                }]),
            )
            clauses(legacy_url, POLICY_ID)
            with psycopg.connect(legacy_url) as connection:
                connection.execute((MIGRATIONS / SOURCE_MIGRATION).read_text())
                self.assertEqual(connection.execute(
                    '''SELECT review_status,publication_status
                       FROM public.policy_catalog_releases WHERE policy_id=%s''',
                    (POLICY_ID,),
                ).fetchone(), ('approved', 'published'))
                self.assertEqual(connection.execute(
                    '''SELECT publication_status FROM public.f13_policy_documents
                       WHERE policy_id=%s AND document_version='fictional-v1' ''',
                    (POLICY_ID,),
                ).fetchone(), ('published',))

    def test_official_only_unchanged_keeps_review_and_least_privilege_collector(self) -> None:
        fields = official_fields(HTML_V1)
        self.assertEqual(fields['raw_sha256'], hashlib.sha256(HTML_V1).hexdigest())
        text = fields['text']
        self.assertIsInstance(text, str)
        self.assertEqual(fields['text_sha256'], hashlib.sha256(text.encode()).hexdigest())
        self.assertEqual(fields['source_url'], OFFICIAL_URL)
        self.assertIn('가상 공식 조건', text)
        self.assertNotEqual(
            record_sha256(fields),
            record_sha256(official_fields(HTML_V1, url=OFFICIAL_URL + '-moved')),
        )

        role = 'bf_official_' + uuid4().hex[:12]
        database_name = conninfo_to_dict(self.url)['dbname']
        try:
            with psycopg.connect(self.url) as connection:
                connection.execute(sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD 'fictional-test-only'"
                ).format(sql.Identifier(role)))
                connection.execute(sql.SQL(
                    'GRANT CONNECT,TEMPORARY ON DATABASE {} TO {}'
                ).format(sql.Identifier(database_name), sql.Identifier(role)))
                connection.execute(sql.SQL(
                    'GRANT USAGE ON SCHEMA public TO {}'
                ).format(sql.Identifier(role)))
                connection.execute(sql.SQL(
                    '''GRANT SELECT,INSERT ON public.collected_policy_runs,
                       public.collected_policy_changes TO {}'''
                ).format(sql.Identifier(role)))
                connection.execute(sql.SQL(
                    'GRANT SELECT,INSERT,UPDATE ON public.collected_policies TO {}'
                ).format(sql.Identifier(role)))
            restricted_url = make_conninfo(
                self.url, user=role, password='fictional-test-only'
            )
            restricted = PostgresCollectedPolicies(restricted_url)
            first, digest = self.save_official(fields, AT, store=restricted)
            self.assertEqual(first.new, 1)
            binding = [{
                'source': SOURCE_NAME,
                'external_id': OFFICIAL_ID,
                'material_sha256': digest,
            }]
            publish(self.url, catalog_record(binding))
            clauses(self.url, POLICY_ID)
            task_id, _, output_ref = self.complete_f12(f12_input(digest))

            unchanged, unchanged_digest = self.save_official(
                fields, AT + timedelta(seconds=1), store=restricted
            )
            self.assertEqual((unchanged.unchanged, unchanged_digest), (1, digest))
            self.assertEqual(
                [item.policy_id for item in PostgresPublishedPolicies(self.url).list_published()],
                [POLICY_ID],
            )
            with psycopg.connect(self.url) as connection:
                self.assertEqual(connection.execute(
                    '''SELECT review_status,publication_status FROM public.f13_clauses
                       WHERE policy_id=%s AND document_version='fictional-v1' ''',
                    (POLICY_ID,),
                ).fetchone(), ('approved', 'published'))
                self.assertEqual(connection.execute(
                    'SELECT count(*) FROM public.policy_recheck_queue'
                ).fetchone(), (0,))
            task = self.tasks.get_task(task_id)
            self.assertEqual(task['status'], 'needs_review')
            self.assertEqual(task['output_ref'], output_ref)
            with self.assertRaises(psycopg.errors.InsufficientPrivilege), \
                    psycopg.connect(restricted_url) as connection:
                connection.execute('SELECT * FROM public.policy_catalog_releases')
        finally:
            with psycopg.connect(self.url) as connection:
                connection.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
                connection.execute(sql.SQL('DROP ROLE IF EXISTS {}').format(sql.Identifier(role)))

    def test_official_change_with_unchanged_api_invalidates_each_consumer_once(self) -> None:
        self.collected.save(batch({API_ID: API_VALUE}, AT))
        old_fields = official_fields(HTML_V1)
        _, old_digest = self.save_official(old_fields, AT)
        bindings = [
            {'source': SOURCE_NAME, 'external_id': OFFICIAL_ID,
             'material_sha256': old_digest},
            {'source': 'youthcenter', 'external_id': API_ID,
             'material_sha256': api_digest()},
        ]
        item = catalog_record(bindings)
        publish(self.url, item)
        clauses(self.url, POLICY_ID)
        task_id, _, output_ref = self.complete_f12(
            f12_input(old_digest, include_api=True)
        )

        new_fields = official_fields(HTML_V2)
        changed, new_digest = self.save_official(
            new_fields, AT + timedelta(seconds=10)
        )
        self.assertEqual(changed.changed, 1)
        self.assertNotEqual(new_digest, old_digest)
        self.assertEqual(PostgresPublishedPolicies(self.url).list_published(), ())
        with psycopg.connect(self.url) as connection:
            release = connection.execute(
                '''SELECT review_status,publication_status,approved_sha256,
                          payload#>>'{benefit,source,document_version}'
                   FROM public.policy_catalog_releases WHERE policy_id=%s''',
                (POLICY_ID,),
            ).fetchone()
            self.assertEqual(release[:3], ('pending', 'retired', record_hash(item)))
            self.assertEqual(release[3], 'fictional-v1')
            self.assertEqual(connection.execute(
                '''SELECT review_status,publication_status,body FROM public.f13_clauses
                   WHERE policy_id=%s AND document_version='fictional-v1' ''',
                (POLICY_ID,),
            ).fetchone(), ('pending', 'retired', '가상 근거입니다.'))
            self.assertEqual(connection.execute(
                '''SELECT source,kind,previous_sha256,current_sha256
                   FROM public.collected_policy_changes WHERE kind='changed' '''
            ).fetchall(), [(SOURCE_NAME, 'changed', old_digest, new_digest)])
            self.assertEqual(connection.execute(
                '''SELECT count(*) FROM public.collected_policy_runs
                   WHERE source='youthcenter' '''
            ).fetchone(), (1,))
            self.assertEqual(connection.execute(
                '''SELECT count(*) FROM public.collected_policies
                   WHERE source='youthcenter' AND material_sha256=%s''',
                (api_digest(),),
            ).fetchone(), (1,))
            rechecks = connection.execute(
                '''SELECT source,external_id,reason,approved_material_sha256,
                          current_material_sha256,run_id FROM public.policy_recheck_queue'''
            ).fetchall()
            self.assertEqual(len(rechecks), 1)
            self.assertEqual(rechecks[0][:3], (SOURCE_NAME, OFFICIAL_ID, 'source_changed'))
            self.assertEqual(rechecks[0][3:5], (old_digest, new_digest))
        task = self.tasks.get_task(task_id)
        self.assertEqual(task['status'], 'stale')
        self.assertEqual(task['output_ref'], output_ref)
        self.assertIsNone(self.tasks.claim())

    def test_unreadable_changed_document_retires_old_and_blocks_new_publication(self) -> None:
        _, old_digest = self.save_official(official_fields(HTML_V1), AT)
        bindings = [{'source': SOURCE_NAME, 'external_id': OFFICIAL_ID,
                     'material_sha256': old_digest}]
        publish(self.url, catalog_record(bindings))
        clauses(self.url, POLICY_ID)
        task_id, _, output_ref = self.complete_f12(f12_input(old_digest))

        unreadable = official_fields(HTML_V2)
        unreadable.update(format='pdf', text_status='requires_review', text=None,
                          text_sha256=None)
        result, new_digest = self.save_official(unreadable, AT + timedelta(seconds=10))
        self.assertEqual(result.changed, 1)
        bindings[0]['material_sha256'] = new_digest
        with self.assertRaisesRegex(
            psycopg.Error, 'catalog_official_document_requires_review'
        ):
            publish(self.url, catalog_record(bindings, version='fictional-unreadable-v2'),
                    AT + timedelta(seconds=20))
        self.assertEqual(PostgresPublishedPolicies(self.url).list_published(), ())
        self.assertEqual(self.tasks.get_task(task_id)['status'], 'stale')
        self.assertEqual(self.tasks.get_task(task_id)['output_ref'], output_ref)
        with psycopg.connect(self.url) as connection:
            self.assertEqual(connection.execute(
                'SELECT count(*) FROM public.policy_catalog_releases'
            ).fetchone(), (1,))
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM public.f13_clauses WHERE publication_status='published'"
            ).fetchone(), (0,))

    def test_reappearance_keeps_old_content_retired_without_autoapproval(self) -> None:
        old_fields = official_fields(HTML_V1)
        _, old_digest = self.save_official(old_fields, AT)
        item = catalog_record([{
            'source': SOURCE_NAME,
            'external_id': OFFICIAL_ID,
            'material_sha256': old_digest,
        }])
        publish(self.url, item)
        clauses(self.url, POLICY_ID)
        task_id, _, _ = self.complete_f12(f12_input(old_digest))

        _, changed_digest = self.save_official(
            official_fields(HTML_V2), AT + timedelta(seconds=10)
        )
        returned_digest = self.save_official(old_fields, AT + timedelta(seconds=20))[1]
        self.assertEqual(returned_digest, old_digest)
        self.assertNotEqual(returned_digest, changed_digest)
        self.assertEqual(PostgresPublishedPolicies(self.url).list_published(), ())

        with psycopg.connect(self.url) as connection:
            self.assertEqual(connection.execute(
                '''SELECT review_status,publication_status,approved_sha256
                   FROM public.policy_catalog_releases WHERE policy_id=%s''',
                (POLICY_ID,),
            ).fetchone(), ('pending', 'retired', record_hash(item)))
            self.assertEqual(connection.execute(
                '''SELECT body FROM public.f13_clauses WHERE policy_id=%s
                   AND document_version='fictional-v1' ''',
                (POLICY_ID,),
            ).fetchone(), ('가상 근거입니다.',))
        for statement in (
            "UPDATE public.policy_catalog_releases SET publication_status='published' "
            f"WHERE policy_id='{POLICY_ID}'",
            "UPDATE public.f13_policy_documents SET publication_status='published' "
            f"WHERE policy_id='{POLICY_ID}' AND document_version='fictional-v1'",
            "UPDATE public.f13_clauses SET publication_status='published' "
            f"WHERE policy_id='{POLICY_ID}' AND document_version='fictional-v1'",
        ):
            with self.subTest(statement=statement), self.assertRaises(psycopg.Error), \
                    psycopg.connect(self.url) as connection:
                connection.execute(statement)
        with self.assertRaises(psycopg.Error):
            publish(self.url, item, AT + timedelta(seconds=30))
        self.assertEqual(self.tasks.get_task(task_id)['status'], 'stale')
        self.assertEqual(self.tasks.status()['tasks'], {'stale': 1})

    def test_official_collection_catalog_and_f13_writers_share_one_lock_order(self) -> None:
        self.collected.save(batch({API_ID: API_VALUE}, AT))
        old_fields = official_fields(HTML_V1)
        _, old_digest = self.save_official(old_fields, AT)
        bindings = [
            {'source': SOURCE_NAME, 'external_id': OFFICIAL_ID,
             'material_sha256': old_digest},
            {'source': 'youthcenter', 'external_id': API_ID,
             'material_sha256': api_digest()},
        ]
        publish(self.url, catalog_record(bindings))
        clauses(self.url, POLICY_ID)
        old_hash_version = catalog_record(bindings, version='fictional-race-v2')
        new_fields = official_fields(HTML_V2)
        barrier = threading.Barrier(4)

        def collect_official():
            barrier.wait(timeout=5)
            return self.save_official(new_fields, AT + timedelta(seconds=10))[0]

        def publish_old_hash() -> bool:
            barrier.wait(timeout=5)
            try:
                publish(self.url, old_hash_version, AT + timedelta(seconds=20))
            except psycopg.errors.DeadlockDetected:
                raise
            except psycopg.Error as error:
                if 'catalog_source_is_stale' not in str(error):
                    raise
                return False
            return True

        def write_f13() -> None:
            barrier.wait(timeout=5)
            with psycopg.connect(
                self.url, options='-c lock_timeout=5s -c statement_timeout=10s'
            ) as connection:
                connection.execute(
                    '''INSERT INTO public.f13_policy_documents
                       (policy_id,document_version,policy_name,category,source_url,
                        checked_at,publication_status)
                       VALUES (%s,'fictional-race-draft','가상 경쟁 문서','housing',%s,%s,
                               'draft')''',
                    (POLICY_ID, OFFICIAL_URL, AT + timedelta(seconds=20)),
                )

        with ThreadPoolExecutor(max_workers=3) as executor:
            collection = executor.submit(collect_official)
            publication = executor.submit(publish_old_hash)
            f13_write = executor.submit(write_f13)
            barrier.wait(timeout=5)
            self.assertEqual(collection.result(timeout=10).changed, 1)
            publication.result(timeout=10)
            f13_write.result(timeout=10)

        self.assertEqual(PostgresPublishedPolicies(self.url).list_published(), ())
        with psycopg.connect(self.url) as connection:
            self.assertEqual(connection.execute(
                '''SELECT count(*) FROM public.policy_catalog_releases
                   WHERE publication_status='published' '''
            ).fetchone(), (0,))
            self.assertEqual(connection.execute(
                '''SELECT count(*) FROM public.f13_clauses
                   WHERE publication_status='published' '''
            ).fetchone(), (0,))
            self.assertEqual(connection.execute(
                '''SELECT count(*) FROM public.policy_recheck_queue
                   WHERE source=%s AND approved_material_sha256=%s''',
                (SOURCE_NAME, old_digest),
            ).fetchone(), (1,))
if __name__ == '__main__':
    unittest.main()
