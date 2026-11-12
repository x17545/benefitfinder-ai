"""F14 real PostgreSQL publication, valuation, source changes and HTTP contracts."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from datetime import datetime, timedelta, timezone
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
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb
from fastapi.testclient import TestClient

from backend.integration.test_policy_source_rechecks_postgres import (
    AT, batch, dedicated_database, publish, record,
)
from backend.tests.service_catalog_fixtures import (
    service_release_data, service_source_text, price_source_text,
)
from backend.tests import test_f14_service_api as service_contracts
from benefitfinder.application.published_policies import PolicyCatalogUnavailable
from benefitfinder.application.service_catalog import ServiceCatalog
from benefitfinder.bootstrap import create_app
from benefitfinder.infrastructure.database.repositories.collected_policies import PostgresCollectedPolicies
from benefitfinder.infrastructure.database.repositories.policy_rechecks import list_policy_rechecks
from benefitfinder.infrastructure.database.repositories.published_policies import PostgresPublishedPolicies
from benefitfinder.infrastructure.database.repositories.published_services import (
    PostgresPublishedServices, publish_service,
)
from benefitfinder.infrastructure.service_catalog_records import ServiceCatalogRecord, service_record_hash
from benefitfinder.jobs.__main__ import main

MIGRATION = Path(__file__).resolve().parents[1] / 'migrations/versions/0008_service_catalog.sql'


def versioned(data: dict, version: str) -> dict:
    result = deepcopy(data)
    result['benefit']['source']['document_version'] = version
    evidence = [*result['service_evidence'],
                *[e for c in result['benefit']['eligibility_conditions'] for e in c['evidence']],
                *[e for conflict in result['benefit']['conflicts'] for e in conflict['evidence']]]
    for item in evidence:
        item['source']['document_version'] = version
    return result


class PublishedServicesPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base = os.environ.get('F13_TEST_DATABASE_URL', '')
        if not cls.base or not conninfo_to_dict(cls.base).get('dbname', '').endswith('_test'):
            raise ValueError('dedicated_test_database_required')
        cls.database = dedicated_database(cls.base)
        cls.url = cls.database.__enter__()
        with psycopg.connect(cls.url) as connection:
            connection.execute(MIGRATION.read_text())
        cls.contracts = service_contracts.F14ServiceApiTests()
        cls.contracts.setUpClass()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.database.__exit__(None, None, None)

    def setUp(self) -> None:
        with psycopg.connect(self.url) as connection:
            connection.execute('''TRUNCATE public.policy_recheck_queue,public.policy_catalog_source_links,
                public.policy_catalog_releases,public.collected_policy_changes,public.collected_policies,
                public.collected_policy_runs,public.f13_clauses,public.f13_policy_documents RESTART IDENTITY''')
        self.repository = PostgresPublishedServices(self.url)
        self.payload = json.loads((Path(__file__).resolve().parents[2] /
                                   'contracts/fixtures/recommendation-request.json').read_text())
        self.payload['profile'].update(age=25)
        self.payload['profile']['domain_data']['interests'] = ['employment', 'housing']

    def publish(self, data: dict | None = None, **kwargs) -> ServiceCatalogRecord:
        item = ServiceCatalogRecord.model_validate(data if data is not None else service_release_data())
        publish_service(self.url, item, source_text=service_source_text(),
                        price_source_text=price_source_text() if item.price is not None else None,
                        approved_sha256=service_record_hash(item), reviewer='fictional-human-reviewer',
                        reviewed_at=datetime.now(timezone.utc), **kwargs)
        return item

    def client(self, **kwargs) -> TestClient:
        client = TestClient(create_app(policy_database_url=self.url, **kwargs), raise_server_exceptions=False)
        self.addCleanup(client.close)
        return client

    def recommend(self, client: TestClient) -> dict:
        response = client.post('/api/v1/recommendations', json=self.payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.contracts.assert_contract('recommendation-response', response.json())
        return response.json()

    def test_service_only_catalog_returns_reviewed_value_and_public_detail(self) -> None:
        item = self.publish()
        self.assertEqual(PostgresPublishedPolicies(self.url).list_published(), ())
        client = self.client()
        result = self.recommend(client)
        self.assertEqual(result['services_total_won'], 120_000)
        self.assertEqual(len(result['services']), 1)
        self.assertTrue(result['virtual_data'])
        detail = client.get('/api/v1/benefits/' + item.benefit.id).json()
        self.contracts.assert_contract('benefit', detail)
        self.assertEqual(detail['service'], result['services'][0]['service'])
        self.assertEqual(detail['conflicts'], item.benefit.model_dump()['conflicts'])
        self.assertEqual(detail['eligibility_conditions'][0]['evidence'], item.benefit.model_dump()['eligibility_conditions'][0]['evidence'])
        self.assertNotIn('profile', detail)
        self.assertNotIn('eligibility', detail)
        self.assertEqual(client.get('/api/v1/benefits/youth:example:S1').status_code, 404)
        self.assertEqual(client.get('/api/v1/meta').status_code, 200)

    def test_cash_all_periods_and_present_values_unchanged_by_service(self) -> None:
        publish(self.url, record('A', {}))
        self.publish()
        before = self.recommend(self.client(service_catalog=ServiceCatalog(())))
        after = self.recommend(self.client())
        self.assertEqual(after['results'], before['results'])
        self.assertEqual(after['view'], before['view'])
        self.assertEqual(after['services_total_won'], 120_000)
        self.assertEqual({p.policy_id for p in PostgresPublishedPolicies(self.url).list_published()}, {'youth:catalog:A'})

    def test_missing_price_preserves_service_without_number_or_basis(self) -> None:
        self.publish(service_release_data('unpriced', price=False))
        result = self.recommend(self.client())
        service = result['services'][0]['service']
        self.assertIsNone(result['services_total_won'])
        for field in ('unit_value_won','value_basis','value_source_url','value_checked_at','estimated_value_won'):
            self.assertIsNone(service[field])
        self.assertEqual(service['value_status'], 'unknown')

    def test_published_services_use_same_conservative_eligibility_and_categories(self) -> None:
        data = service_release_data()
        data['benefit']['categories'] = ['employment','housing']
        self.publish(data)
        client = self.client()
        self.payload['profile']['age'] = None
        self.payload['profile']['domain_data']['interests'] = ['housing']
        result = self.recommend(client)
        self.assertEqual(len(result['services']), 1)
        self.assertEqual(result['services'][0]['eligibility'][0]['status'], 'needs_review')
        self.payload['profile']['age'] = 50
        self.assertEqual(self.recommend(client)['services'], [])

    def test_closed_service_remains_public_but_not_recommended(self) -> None:
        data = service_release_data()
        data['benefit']['application']['ends_on'] = '2020-01-01'
        item = self.publish(data)
        client = self.client()
        self.assertEqual(self.recommend(client)['services'], [])
        detail = client.get('/api/v1/benefits/'+item.benefit.id)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()['application']['status'], 'closed')

    def test_retired_service_disappears_from_live_instance(self) -> None:
        item = self.publish()
        client = self.client()
        self.assertEqual(len(self.recommend(client)['services']), 1)
        with psycopg.connect(self.url) as connection:
            connection.execute("UPDATE public.policy_catalog_releases SET publication_status='retired',review_status='pending'")
        self.assertEqual(self.recommend(client)['services'], [])
        self.assertEqual(client.get('/api/v1/benefits/'+item.benefit.id).status_code, 404)
        self.assertEqual(client.get('/api/v1/meta').status_code, 503)

    def test_changed_price_needs_new_whole_record_approval_and_version(self) -> None:
        item = self.publish()
        changed = versioned(service_release_data(), 'fictional-v2')
        changed['price']['unit_value_won'] = 45_000
        new = ServiceCatalogRecord.model_validate(changed)
        with self.assertRaisesRegex(ValueError, 'invalid_service_approval'):
            publish_service(self.url,new,source_text=service_source_text(),price_source_text=price_source_text(),
                            approved_sha256=service_record_hash(item),reviewer='fictional-human-reviewer',reviewed_at=datetime.now(timezone.utc))
        self.assertEqual(self.repository.get_published(item.benefit.id).price.unit_value_won, 40_000)
        self.publish(changed)
        self.assertEqual(self.recommend(self.client())['services_total_won'], 135_000)

    def test_duplicate_version_failure_rolls_back_retirement(self) -> None:
        self.publish()
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.publish()
        self.assertEqual(len(self.repository.list_published()), 1)

    def test_concurrent_version_publications_keep_one_live_service(self) -> None:
        self.publish()
        with ThreadPoolExecutor(max_workers=2) as executor:
            releases = list(executor.map(self.publish, (
                versioned(service_release_data(), 'fictional-v2'),
                versioned(service_release_data(), 'fictional-v3'),
            )))
        self.assertEqual(len(releases), 2)
        self.assertEqual(len(self.repository.list_published()), 1)
        with psycopg.connect(self.url) as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM public.policy_catalog_releases').fetchone()[0], 3)

    def test_policy_cannot_change_between_cash_and_service_across_versions(self) -> None:
        publish(self.url, record('A', {}))
        data = versioned(service_release_data(), 'fictional-service-v2')
        data['benefit']['id'] = 'youth:catalog:A'
        data['benefit']['conflicts'] = []
        with self.assertRaisesRegex(psycopg.Error, 'catalog_policy_kind_is_immutable'):
            self.publish(data)
        self.assertEqual(len(PostgresPublishedPolicies(self.url).list_published()), 1)
        self.assertEqual(self.repository.list_published(), ())

    def test_price_source_change_or_approval_before_price_cannot_publish(self) -> None:
        item = ServiceCatalogRecord.model_validate(service_release_data())
        for price_text, reviewed in ((price_source_text()+'changed',datetime.now(timezone.utc)),
                                     (price_source_text(),datetime(2000,1,1,tzinfo=timezone.utc))):
            with self.assertRaises(ValueError):
                publish_service(self.url,item,source_text=service_source_text(),price_source_text=price_text,
                                approved_sha256=service_record_hash(item),reviewer='fictional-human-reviewer',reviewed_at=reviewed)
        self.assertEqual(self.repository.list_published(), ())

    def test_source_change_retires_service_and_recheck_handoff_in_same_commit(self) -> None:
        collected = PostgresCollectedPolicies(self.url)
        collected.save(batch({'service-source': 'fictional original'}, AT))
        data = service_release_data()
        data['benefit']['virtual_data'] = False
        data['collection_sources'] = [{'source':'youthcenter','external_id':'service-source',
            'material_sha256':hashlib.sha256(b'fictional original').hexdigest()}]
        item = self.publish(data)
        client = self.client()
        self.assertFalse(self.recommend(client)['virtual_data'])
        collected.save(batch({'service-source':'fictional original'},AT+timedelta(seconds=1)))
        self.assertEqual(len(self.repository.list_published()),1)
        collected.save(batch({'service-source':'fictional change'},AT+timedelta(seconds=2)))
        self.assertEqual(self.recommend(client)['services'], [])
        self.assertEqual(client.get('/api/v1/benefits/'+item.benefit.id).status_code,404)
        page = list_policy_rechecks(self.url)
        self.assertEqual(page['items'][0]['reason'],'source_changed')
        self.assertEqual(page['items'][0]['policy_id'], item.benefit.id)

    def test_read_only_role_reads_but_cannot_publish(self) -> None:
        self.publish()
        role = 'bf_service_reader_'+uuid4().hex
        with psycopg.connect(self.url) as connection:
            connection.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'fictional-test-only'").format(sql.Identifier(role)))
            connection.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(role)))
            connection.execute(sql.SQL('GRANT SELECT ON public.policy_catalog_releases TO {}').format(sql.Identifier(role)))
        reader_url = make_conninfo(self.url, user=role, password='fictional-test-only')
        try:
            self.assertEqual(len(PostgresPublishedServices(reader_url).list_published()), 1)
            with self.assertRaises(psycopg.errors.InsufficientPrivilege), psycopg.connect(reader_url) as connection:
                connection.execute('DELETE FROM public.policy_catalog_releases')
        finally:
            with psycopg.connect(self.url) as connection:
                connection.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
                connection.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))

    def test_missing_source_and_reappearance_do_not_reapprove_service(self) -> None:
        collected = PostgresCollectedPolicies(self.url)
        collected.save(batch({'service-source':'fictional original'}, AT))
        data = service_release_data()
        data['collection_sources'] = [{'source':'youthcenter','external_id':'service-source',
            'material_sha256':hashlib.sha256(b'fictional original').hexdigest()}]
        self.publish(data)
        collected.save(batch({},AT+timedelta(seconds=1)))
        self.assertEqual(self.repository.list_published(), ())
        collected.save(batch({'service-source':'fictional original'},AT+timedelta(seconds=2)))
        self.assertEqual(self.repository.list_published(), ())
        self.assertEqual(list_policy_rechecks(self.url)['items'][0]['reason'], 'source_missing')

    def test_publisher_role_needs_no_source_table_or_owner_permissions(self) -> None:
        collected = PostgresCollectedPolicies(self.url)
        collected.save(batch({'service-source':'fictional original'}, AT))
        data = service_release_data()
        data['benefit']['virtual_data'] = False
        data['collection_sources'] = [{'source':'youthcenter','external_id':'service-source',
            'material_sha256':hashlib.sha256(b'fictional original').hexdigest()}]
        item = ServiceCatalogRecord.model_validate(data)
        role = 'bf_service_publisher_'+uuid4().hex
        with psycopg.connect(self.url) as connection:
            connection.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'fictional-test-only'").format(sql.Identifier(role)))
            for statement in (
                'GRANT USAGE ON SCHEMA public TO {}',
                'GRANT SELECT,INSERT ON public.policy_catalog_releases TO {}',
                'GRANT UPDATE (publication_status) ON public.policy_catalog_releases TO {}',
                'GRANT SELECT (version) ON public.benefitfinder_schema_migrations TO {}',
            ):
                connection.execute(sql.SQL(statement).format(sql.Identifier(role)))
        publisher_url = make_conninfo(self.url,user=role,password='fictional-test-only')
        try:
            for value in (item, ServiceCatalogRecord.model_validate(versioned(data,'fictional-v2'))):
                publish_service(publisher_url,value,source_text=service_source_text(),
                    price_source_text=price_source_text(),approved_sha256=service_record_hash(value),
                    reviewer='fictional-human-reviewer',reviewed_at=datetime.now(timezone.utc))
            self.assertEqual(len(self.repository.list_published()),1)
            with self.assertRaises(psycopg.errors.InsufficientPrivilege), psycopg.connect(publisher_url) as connection:
                connection.execute('SELECT material_sha256 FROM public.collected_policies')
            collected.save(batch({'service-source':'fictional change'},AT+timedelta(seconds=1)))
            self.assertEqual(self.repository.list_published(),())
        finally:
            with psycopg.connect(self.url) as connection:
                connection.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
                connection.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))

    def test_service_cli_validates_both_sources_and_requires_separate_approval(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = service_release_data()
            item = ServiceCatalogRecord.model_validate(data)
            (root/'record.json').write_text(json.dumps(data))
            (root/'source.txt').write_text(service_source_text())
            (root/'price.txt').write_text(price_source_text())
            (root/'approval.json').write_text(json.dumps({'record_sha256':service_record_hash(item),
                'reviewed_by':'fictional-human-reviewer','reviewed_at':datetime.now(timezone.utc).isoformat()}))
            output = StringIO()
            args = ['--input',str(root/'record.json'),'--source',str(root/'source.txt'),'--price-source',str(root/'price.txt')]
            with patch.dict(os.environ,{'POLICY_PUBLISH_DATABASE_URL':self.url}), redirect_stdout(output),redirect_stderr(output):
                self.assertEqual(main(['service-catalog','validate',*args]),0)
                self.assertEqual(main(['service-catalog','publish',*args,'--approval',str(root/'approval.json')]),0)
            self.assertEqual(len(self.repository.list_published()),1)
            self.assertNotIn(service_source_text(), output.getvalue())

    def test_lookup_failure_is_safe_503_without_examples_or_raw_inputs(self) -> None:
        item = ServiceCatalogRecord.model_validate(service_release_data())
        with psycopg.connect(self.url) as connection:
            connection.execute('''INSERT INTO public.policy_catalog_releases
                (policy_id,document_version,payload,approved_sha256,review_status,reviewed_by,
                 reviewed_at,publication_status,published_at) VALUES (%s,%s,%s,%s,'approved',
                 'fictional-human-reviewer',%s,'published',clock_timestamp())''',
                 (item.benefit.id,item.benefit.source.document_version,Jsonb(item.model_dump(mode='json')),
                  '0'*64, AT))
        with self.assertRaises(PolicyCatalogUnavailable):
            self.repository.list_published()
        client = self.client()
        for response in (client.post('/api/v1/recommendations',json=self.payload),
                         client.get('/api/v1/benefits/'+item.benefit.id),client.get('/api/v1/meta')):
            self.assertEqual(response.status_code,503)
            self.assertNotIn('fictional-human-reviewer',response.text)
            self.assertNotIn('postgres', response.text.lower())
