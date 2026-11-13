"""Automatic API plus collected official HTML/PDF preparation in PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from backend.integration.test_policy_source_rechecks_postgres import dedicated_database
from backend.tests.test_policy_preparation import fields, plan_data
from benefitfinder.application.collected_policies import CollectedPolicyBatch
from benefitfinder.application.collection import CollectedRecord, SourceResponse
from benefitfinder.application.official_documents import (
    SOURCE_NAME, OfficialDocumentTarget,
)
from benefitfinder.application.policy_analysis import (
    AnalysisDocumentSource, PolicyAnalysisStoreError,
)
from benefitfinder.application.policy_rules import SourceDocument
from benefitfinder.application.prepare_policy_analysis import prepare_analysis_inputs
from benefitfinder.infrastructure.database.repositories.analysis_sources import (
    PostgresAnalysisSources,
)
from benefitfinder.infrastructure.database.repositories.collected_policies import (
    PostgresCollectedPolicies,
)
from benefitfinder.infrastructure.database.repositories.policy_analysis import (
    PostgresPolicyAnalysisTasks,
)
from benefitfinder.infrastructure.policy_analysis_inputs import analysis_input_from_payload
from benefitfinder.infrastructure.policy_preparation_plans import preparation_plan_from_payload
from benefitfinder.ingestion.official_documents import (
    document_fields, official_document_material_hash,
)
from benefitfinder.ingestion.versioning import record_sha256
from benefitfinder.ingestion.youthcenter import YOUTHCENTER_POLICY_LIST, hashed_fields
from benefitfinder.jobs.__main__ import main


MIGRATIONS = Path(__file__).resolve().parents[1] / 'migrations/versions'
POST_0006_MIGRATIONS = (
    '0007_f12_analysis_tasks.sql',
    '0008_service_catalog.sql',
    '0009_gov24_policy_sources.sql',
    '0010_catalog_explanation_publication.sql',
    '0011_f12_analysis_versions.sql',
    '0012_official_document_sources.sql',
)
AT = datetime(2026, 11, 2, 1, tzinfo=timezone.utc)
OFFICIAL_AT = AT + timedelta(seconds=10)
POLICY_ID = 'youth:fictional:A'
API_ID = 'A'
HTML_ID = 'notice:fictional:A-html'
PDF_ID = 'notice:fictional:A-pdf'
HTML_DOCUMENT_ID = 'official:notice:A'
PDF_DOCUMENT_ID = 'official:pdf:A'
HTML_URL = 'https://example.test/fictional-policy-notice.html'
PDF_URL = 'https://example.test/fictional-policy-notice.pdf'
HTML_V1 = (
    b'<html><body><main><p>'
    + '가상 청년 취업 A: 만 19세 이상 청년에게 20만 원을 지원합니다.'.encode()
    + b'</p></main></body></html>'
)
HTML_V2 = (
    b'<html><body><main><p>'
    + '가상 청년 취업 A: 만 19세 이상, 소득 기준이 변경되었습니다.'.encode()
    + b'</p></main></body></html>'
)
PDF_V1 = b'%PDF-fictional-official-v1'
PDF_PAGES = ('가상 청년 취업 PDF 보충: 만 19세 이상 청년 대상',)
HTML_TARGET = OfficialDocumentTarget(
    HTML_ID, POLICY_ID, HTML_URL, 'html', categories=('employment',),
    required_text=('가상 청년 취업',),
)
PDF_TARGET = OfficialDocumentTarget(
    PDF_ID, POLICY_ID, PDF_URL, 'pdf', categories=('employment',),
    required_text=('가상 청년 취업 PDF',),
)
UNREADABLE_TARGET = replace(
    HTML_TARGET, format='pdf', url='https://example.test/fictional-unreadable.pdf',
)


def api_material_hash(data: dict[str, object]) -> str:
    return record_sha256(hashed_fields(YOUTHCENTER_POLICY_LIST, data))


def official_fields(
    target: OfficialDocumentTarget,
    body: bytes,
    pdf_pages=lambda _body: (),
) -> dict[str, object]:
    content_type = 'application/pdf' if target.format == 'pdf' else 'text/html; charset=utf-8'
    return document_fields(
        target, SourceResponse(200, body, content_type), pdf_pages,
    )


def unreadable_pdf(_body: bytes):
    raise ValueError('fictional PDF text is unavailable')


class FrozenSources:
    def __init__(self, snapshot):
        self._snapshot = snapshot

    def load(self, _external_ids: tuple[str, ...]):
        return self._snapshot


class OfficialDocumentPreparationPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base = os.environ.get('F13_TEST_DATABASE_URL', '')
        if not cls.base or not conninfo_to_dict(cls.base).get('dbname', '').endswith('_test'):
            raise ValueError('dedicated_test_database_required')
        cls.database = dedicated_database(cls.base)
        cls.url = cls.database.__enter__()
        with psycopg.connect(cls.url) as connection:
            for name in POST_0006_MIGRATIONS:
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
        self.api_sources = PostgresAnalysisSources(self.url)
        self.official_sources = PostgresAnalysisSources(self.url, source=SOURCE_NAME)

    def save_api(self, data: dict[str, object], at: datetime) -> None:
        digest = api_material_hash(data)
        record = CollectedRecord(
            'youthcenter', API_ID, digest,
            f'youthcenter/snapshots/fictional-{digest[:12]}.json',
            at - timedelta(milliseconds=100), data,
        )
        self.collected.save(CollectedPolicyBatch(
            'youthcenter', uuid4().hex, at, (record,),
        ))

    def save_official(
        self,
        items: tuple[tuple[str, dict[str, object]], ...],
        at: datetime,
    ) -> None:
        records = []
        for external_id, data in items:
            digest = official_document_material_hash(data)
            records.append(CollectedRecord(
                SOURCE_NAME, external_id, digest,
                f'official_documents/snapshots/fictional-{external_id}-{digest[:12]}.body',
                at - timedelta(milliseconds=100), data,
            ))
        self.collected.save(CollectedPolicyBatch(
            SOURCE_NAME, uuid4().hex, at, tuple(records),
        ))

    @staticmethod
    def combined_plan(*mappings: tuple[str, str]) -> dict:
        raw = plan_data()
        raw['targets'][0]['official_documents'] = [
            {'external_id': external_id, 'document_id': document_id}
            for external_id, document_id in mappings
        ]
        return raw

    def prepare(
        self,
        raw: dict,
        *,
        capture: list | None = None,
        official_loader=None,
    ) -> dict[str, int]:
        plan = preparation_plan_from_payload(raw)

        def register(value):
            if capture is not None:
                capture.append(value)
            return self.tasks.register_prepared(value)

        if any(target.official_documents for target in plan.targets):
            return prepare_analysis_inputs(
                plan, self.api_sources, register, api_material_hash,
                official_sources=official_loader or self.official_sources,
                official_material_hash=official_document_material_hash,
            )
        return prepare_analysis_inputs(
            plan, self.api_sources, register, api_material_hash,
        )

    def task(self, task_id: int) -> dict | None:
        with psycopg.connect(self.url, row_factory=dict_row) as connection:
            return connection.execute(
                '''SELECT task_id,status,attempts,input_sha256,input_payload,
                   output_ref,output_sha256,last_error
                   FROM public.f12_analysis_tasks WHERE task_id=%s''',
                (task_id,),
            ).fetchone()

    def latest(self):
        with psycopg.connect(self.url, row_factory=dict_row) as connection:
            row = connection.execute(
                '''SELECT task_id,status,attempts,input_sha256,input_payload,
                   output_ref,output_sha256,last_error
                   FROM public.f12_analysis_tasks ORDER BY task_id DESC LIMIT 1'''
            ).fetchone()
        return row, analysis_input_from_payload(row['input_payload'])

    def complete_next(self):
        claim = self.tasks.claim()
        self.assertIsNotNone(claim)
        output_ref = f'f12-analysis/{claim.task_id}/{claim.token}'
        self.assertEqual(
            self.tasks.complete(
                claim,
                output_ref=output_ref,
                output_sha256='f' * 64,
                summary={'rules': 0},
            ),
            'needs_review',
        )
        return claim, output_ref

    @staticmethod
    def manual_document(document_id: str, at: datetime, text: str) -> SourceDocument:
        return SourceDocument(
            document_id, POLICY_ID,
            'https://example.test/' + document_id.replace(':', '-'),
            'manual-v1', at, text, True,
        )

    def seed_combined(self) -> dict:
        self.save_api(fields(), AT)
        self.save_official(((HTML_ID, official_fields(HTML_TARGET, HTML_V1)),), OFFICIAL_AT)
        return self.combined_plan((HTML_ID, HTML_DOCUMENT_ID))

    def test_api_and_collected_html_pdf_keep_both_actual_acceptance_times(self) -> None:
        self.save_api(fields(), AT)
        html = official_fields(HTML_TARGET, HTML_V1)
        pdf = official_fields(PDF_TARGET, PDF_V1, lambda _body: PDF_PAGES)
        self.save_official(((HTML_ID, html), (PDF_ID, pdf)), OFFICIAL_AT)
        raw = self.combined_plan(
            (HTML_ID, HTML_DOCUMENT_ID), (PDF_ID, PDF_DOCUMENT_ID),
        )
        captured = []
        self.assertEqual(
            self.prepare(raw, capture=captured),
            {'targets': 1, 'registered': 1, 'replayed': 0, 'failed': 0},
        )
        value = captured[0]
        api_document, html_document, pdf_document = value.documents
        self.assertEqual(api_document.collected_at, AT)
        self.assertEqual(html_document.collected_at, OFFICIAL_AT)
        self.assertEqual(pdf_document.collected_at, OFFICIAL_AT)
        self.assertEqual(html_document.text, html['text'])
        self.assertEqual(pdf_document.text, pdf['text'])
        self.assertEqual(
            html_document.document_version,
            'official-v1:' + official_document_material_hash(html),
        )
        self.assertEqual(
            pdf_document.document_version,
            'official-v1:' + official_document_material_hash(pdf),
        )
        self.assertEqual(
            set(value.document_sources),
            {
                AnalysisDocumentSource(api_document.id, 'youthcenter', API_ID),
                AnalysisDocumentSource(HTML_DOCUMENT_ID, SOURCE_NAME, HTML_ID),
                AnalysisDocumentSource(PDF_DOCUMENT_ID, SOURCE_NAME, PDF_ID),
            },
        )
        claim = self.tasks.claim()
        self.assertIsNotNone(claim)
        self.assertEqual(
            [document.collected_at for document in claim.input.documents],
            [AT, OFFICIAL_AT, OFFICIAL_AT],
        )
        with psycopg.connect(self.url) as connection:
            self.assertEqual(connection.execute(
                'SELECT count(*) FROM public.policy_catalog_releases'
            ).fetchone()[0], 0)
            self.assertEqual(connection.execute(
                'SELECT count(*) FROM public.f13_clauses'
            ).fetchone()[0], 0)

    def test_unchanged_recollection_replays_completed_supplemented_task(self) -> None:
        raw = self.seed_combined()
        self.assertEqual(self.prepare(raw)['registered'], 1)
        claim, output_ref = self.complete_next()
        before = self.task(claim.task_id)

        self.save_api(fields(), AT + timedelta(seconds=20))
        self.save_official(
            ((HTML_ID, official_fields(HTML_TARGET, HTML_V1)),),
            AT + timedelta(seconds=21),
        )
        self.assertEqual(
            self.prepare(raw),
            {'targets': 1, 'registered': 0, 'replayed': 1, 'failed': 0},
        )
        self.assertEqual(self.task(claim.task_id), before)
        self.assertEqual(before['output_ref'], output_ref)
        self.assertIsNone(self.tasks.claim())
        self.assertEqual(self.tasks.status()['tasks'], {'needs_review': 1})

    def test_api_only_legacy_payload_has_no_bindings_and_still_replays(self) -> None:
        self.save_api(fields(), AT)
        captured = []
        self.assertEqual(self.prepare(plan_data(), capture=captured)['registered'], 1)
        value = captured[0]
        self.assertEqual(value.document_sources, ())
        self.assertNotIn('document_sources', value.payload())
        row, stored = self.latest()
        self.assertNotIn('document_sources', row['input_payload'])
        self.assertEqual(row['input_sha256'], stored.fingerprint())
        claim, _output_ref = self.complete_next()
        before = self.task(claim.task_id)

        self.save_api(fields(), AT + timedelta(seconds=20))
        self.assertEqual(
            self.prepare(plan_data()),
            {'targets': 1, 'registered': 0, 'replayed': 1, 'failed': 0},
        )
        self.assertEqual(self.task(claim.task_id), before)
        self.assertIsNone(self.tasks.claim())

    def test_official_only_change_stales_completed_task_and_registers_new_input(self) -> None:
        raw = self.seed_combined()
        self.prepare(raw)
        old_claim, old_output = self.complete_next()
        self.save_official(
            ((HTML_ID, official_fields(HTML_TARGET, HTML_V2)),),
            AT + timedelta(seconds=20),
        )

        self.assertEqual(
            self.prepare(raw),
            {'targets': 1, 'registered': 1, 'replayed': 0, 'failed': 0},
        )
        old = self.task(old_claim.task_id)
        self.assertEqual(old['status'], 'stale')
        self.assertEqual(old['last_error'], 'f12_source_changed')
        self.assertEqual(old['output_ref'], old_output)
        latest, value = self.latest()
        self.assertNotEqual(latest['task_id'], old_claim.task_id)
        self.assertEqual(latest['status'], 'pending')
        official = next(document for document in value.documents if document.id == HTML_DOCUMENT_ID)
        self.assertEqual(official.collected_at, AT + timedelta(seconds=20))
        self.assertIn('소득 기준이 변경', official.text)
        claim = self.tasks.claim()
        self.assertEqual(claim.task_id, latest['task_id'])

    def test_api_only_change_requeues_and_preserves_official_acceptance_time(self) -> None:
        raw = self.seed_combined()
        self.prepare(raw)
        old_claim, _old_output = self.complete_next()
        changed = fields(body='가상: 변경된 30만 원을 지원합니다.')
        self.save_api(changed, AT + timedelta(seconds=20))

        self.assertEqual(self.prepare(raw)['registered'], 1)
        self.assertEqual(self.task(old_claim.task_id)['status'], 'stale')
        latest, value = self.latest()
        self.assertEqual(latest['status'], 'pending')
        api_document = next(document for document in value.documents if document.id != HTML_DOCUMENT_ID)
        official = next(document for document in value.documents if document.id == HTML_DOCUMENT_ID)
        self.assertEqual(api_document.collected_at, AT + timedelta(seconds=20))
        self.assertIn('30만 원', api_document.text)
        self.assertEqual(official.collected_at, OFFICIAL_AT)

    def test_missing_official_document_fails_without_replacing_review(self) -> None:
        raw = self.seed_combined()
        self.prepare(raw)
        claim, output_ref = self.complete_next()
        before = self.task(claim.task_id)
        self.save_official((), AT + timedelta(seconds=20))

        self.assertEqual(self.prepare(raw)['failed'], 1)
        after = self.task(claim.task_id)
        self.assertEqual(after['status'], 'stale')
        self.assertEqual(after['last_error'], 'f12_source_changed')
        for key in ('attempts', 'input_sha256', 'output_ref', 'output_sha256'):
            self.assertEqual(after[key], before[key])
        self.assertEqual(after['output_ref'], output_ref)
        self.assertFalse(self.tasks.get_task(claim.task_id)['source_current'])
        self.assertEqual(self.tasks.status()['tasks'], {'stale': 1})

    def test_unreadable_official_document_fails_without_partial_task(self) -> None:
        raw = self.seed_combined()
        self.prepare(raw)
        claim, output_ref = self.complete_next()
        before = self.task(claim.task_id)
        unreadable = official_fields(
            UNREADABLE_TARGET, b'%PDF-fictional-unreadable', unreadable_pdf,
        )
        self.assertEqual(unreadable['text_status'], 'requires_review')
        self.save_official(((HTML_ID, unreadable),), AT + timedelta(seconds=20))

        self.assertEqual(self.prepare(raw)['failed'], 1)
        after = self.task(claim.task_id)
        self.assertEqual(after['status'], 'stale')
        self.assertEqual(after['last_error'], 'f12_source_changed')
        for key in ('attempts', 'input_sha256', 'output_ref', 'output_sha256'):
            self.assertEqual(after[key], before[key])
        self.assertEqual(after['output_ref'], output_ref)
        self.assertEqual(self.tasks.status()['tasks'], {'stale': 1})

    def test_cross_policy_official_document_fails_without_partial_task(self) -> None:
        raw = self.seed_combined()
        self.prepare(raw)
        claim, output_ref = self.complete_next()
        before = self.task(claim.task_id)
        cross_policy = official_fields(
            replace(HTML_TARGET, policy_id='youth:fictional:B'), HTML_V1,
        )
        self.save_official(((HTML_ID, cross_policy),), AT + timedelta(seconds=20))

        self.assertEqual(self.prepare(raw)['failed'], 1)
        after = self.task(claim.task_id)
        self.assertEqual(after['status'], 'stale')
        self.assertEqual(after['last_error'], 'f12_source_changed')
        for key in ('attempts', 'input_sha256', 'output_ref', 'output_sha256'):
            self.assertEqual(after[key], before[key])
        self.assertEqual(after['output_ref'], output_ref)
        self.assertEqual(self.tasks.status()['tasks'], {'stale': 1})

    def test_deletion_and_reappearance_create_a_fresh_task_not_reactivation(self) -> None:
        raw = self.seed_combined()
        self.prepare(raw)
        old_claim, old_output = self.complete_next()
        self.save_official((), AT + timedelta(seconds=20))
        self.assertEqual(self.prepare(raw)['failed'], 1)
        self.save_official(
            ((HTML_ID, official_fields(HTML_TARGET, HTML_V1)),),
            AT + timedelta(seconds=30),
        )

        self.assertEqual(self.prepare(raw)['registered'], 1)
        old = self.task(old_claim.task_id)
        self.assertEqual(old['status'], 'stale')
        self.assertEqual(old['output_ref'], old_output)
        latest, value = self.latest()
        self.assertNotEqual(latest['task_id'], old_claim.task_id)
        self.assertEqual(latest['status'], 'pending')
        official = next(document for document in value.documents if document.id == HTML_DOCUMENT_ID)
        self.assertEqual(official.collected_at, AT + timedelta(seconds=30))
        self.assertEqual(
            official.document_version,
            'official-v1:' + official_document_material_hash(
                official_fields(HTML_TARGET, HTML_V1)
            ),
        )

    def test_stale_official_snapshot_is_rejected_at_transactional_register(self) -> None:
        raw = self.seed_combined()
        old_official = self.official_sources.load((HTML_ID,))
        self.save_official(
            ((HTML_ID, official_fields(HTML_TARGET, HTML_V2)),),
            AT + timedelta(seconds=20),
        )

        self.assertEqual(
            self.prepare(raw, official_loader=FrozenSources(old_official))['failed'],
            1,
        )
        self.assertEqual(self.tasks.status()['tasks'], {})
        with psycopg.connect(self.url) as connection:
            self.assertEqual(connection.execute(
                'SELECT count(*) FROM public.f12_analysis_tasks'
            ).fetchone()[0], 0)

    def test_concurrent_preparation_registers_once_and_replays_once(self) -> None:
        raw = self.seed_combined()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _index: self.prepare(raw), range(2)))
        self.assertEqual(sum(result['registered'] for result in results), 1)
        self.assertEqual(sum(result['replayed'] for result in results), 1)
        self.assertEqual(sum(result['failed'] for result in results), 0)
        self.assertEqual(self.tasks.status()['tasks'], {'pending': 1})

    def test_public_prepare_cli_uses_official_sources_without_llm(self) -> None:
        raw = self.seed_combined()
        with TemporaryDirectory() as temporary:
            plan_path = Path(temporary) / 'plan.json'
            plan_path.write_text(json.dumps(raw))
            output = StringIO()
            errors = StringIO()
            with patch.dict(os.environ, {
                'F12_WRITE_DATABASE_URL': self.url,
                'LLM_PROVIDER': 'disabled',
            }, clear=True), patch(
                'benefitfinder.jobs.__main__.LLMSettings.from_env',
                side_effect=AssertionError('prepare must not initialize an LLM'),
            ), redirect_stdout(output), redirect_stderr(errors):
                self.assertEqual(main([
                    'f12-analysis', 'prepare', '--plan', str(plan_path),
                ]), 0)
        counts = {
            'targets': 1, 'registered': 1, 'replayed': 0, 'failed': 0,
        }
        self.assertEqual(json.loads(output.getvalue()), counts)
        self.assertEqual(errors.getvalue(), '')
        self.assertNotIn(fields()['support'], output.getvalue())
        self.assertNotIn(HTML_URL, output.getvalue())
        self.assertEqual(self.tasks.status()['tasks'], {'pending': 1})

    def test_retention_keeps_a_fresh_unmapped_manual_supplement(self) -> None:
        raw = self.seed_combined()
        self.prepare(raw)
        first_claim, _first_output = self.complete_next()
        _, value = self.latest()
        manual = self.manual_document(
            'manual:supplement:fresh', OFFICIAL_AT, '가상 수동 보충: 최신 확인일입니다.',
        )
        self.assertFalse(self.tasks.register(
            replace(value, documents=value.documents + (manual,))
        )['replayed'])
        manual_claim, _manual_output = self.complete_next()
        before = self.task(manual_claim.task_id)

        self.assertEqual(self.prepare(raw)['replayed'], 1)
        self.assertEqual(self.task(manual_claim.task_id), before)
        self.assertEqual(before['status'], 'needs_review')
        _, stored = self.latest()
        self.assertEqual([document.id for document in stored.documents], [
            value.documents[0].id, HTML_DOCUMENT_ID, manual.id,
        ])
        self.assertEqual(
            {binding.document_id for binding in stored.document_sources},
            {value.documents[0].id, HTML_DOCUMENT_ID},
        )
        self.assertNotEqual(first_claim.task_id, manual_claim.task_id)
        self.assertIsNone(self.tasks.claim())

    def test_older_unmapped_manual_supplement_requires_refresh(self) -> None:
        self.save_api(fields(), AT)
        self.prepare(plan_data())
        self.complete_next()
        _, api_value = self.latest()
        manual = self.manual_document(
            'manual:supplement:older', AT, '가상 수동 보충: 이전 확인일입니다.',
        )
        self.tasks.register(replace(api_value, documents=api_value.documents + (manual,)))
        manual_claim, manual_output = self.complete_next()
        before = self.task(manual_claim.task_id)

        self.save_official(
            ((HTML_ID, official_fields(HTML_TARGET, HTML_V1)),), OFFICIAL_AT,
        )
        raw = self.combined_plan((HTML_ID, HTML_DOCUMENT_ID))
        captured = []
        self.assertEqual(self.prepare(raw, capture=captured)['failed'], 1)
        self.assertEqual(self.task(manual_claim.task_id), before)
        self.assertEqual(before['status'], 'needs_review')
        self.assertEqual(before['output_ref'], manual_output)
        with self.assertRaisesRegex(
            PolicyAnalysisStoreError, 'f12_documents_require_refresh'
        ):
            self.tasks.register_prepared(captured[0])

        refreshed = replace(
            captured[0],
            documents=captured[0].documents + (
                replace(manual, collected_at=OFFICIAL_AT),
            ),
        )
        self.assertFalse(self.tasks.register_prepared(refreshed)['replayed'])
        claim = self.tasks.claim()
        stored_manual = next(document for document in claim.input.documents if document.id == manual.id)
        self.assertEqual(stored_manual.collected_at, OFFICIAL_AT)
        self.assertEqual(len(claim.input.documents), 3)

    def test_exact_document_id_replaces_legacy_manual_supplement_without_duplication(self) -> None:
        self.save_api(fields(), AT)
        self.save_official(((HTML_ID, official_fields(HTML_TARGET, HTML_V1)),), OFFICIAL_AT)
        self.prepare(plan_data())
        self.complete_next()
        _, api_value = self.latest()
        manual_id = 'supplement:notice'
        manual = self.manual_document(manual_id, AT, '가상 기존 수동 보충 문서입니다.')
        self.tasks.register(replace(api_value, documents=api_value.documents + (manual,)))
        manual_claim, manual_output = self.complete_next()
        manual_before = self.task(manual_claim.task_id)

        raw = self.combined_plan((HTML_ID, manual_id))
        captured = []
        self.assertEqual(self.prepare(raw, capture=captured)['registered'], 1)
        old = self.task(manual_claim.task_id)
        self.assertEqual(old['status'], 'stale')
        self.assertEqual(old['output_ref'], manual_output)
        latest, value = self.latest()
        self.assertEqual(latest['status'], 'pending')
        self.assertNotEqual(latest['task_id'], manual_claim.task_id)
        self.assertEqual([document.id for document in value.documents], [
            captured[0].documents[0].id, manual_id,
        ])
        self.assertEqual(len(value.documents), 2)
        official = next(document for document in value.documents if document.id == manual_id)
        self.assertEqual(official.text, official_fields(HTML_TARGET, HTML_V1)['text'])
        self.assertEqual(
            {binding.document_id for binding in value.document_sources},
            {captured[0].documents[0].id, manual_id},
        )
        self.assertNotEqual(manual_before['input_sha256'], latest['input_sha256'])

        claim, _new_output = self.complete_next()
        before_replay = self.task(claim.task_id)
        self.save_api(fields(), AT + timedelta(seconds=20))
        self.save_official(
            ((HTML_ID, official_fields(HTML_TARGET, HTML_V1)),),
            AT + timedelta(seconds=21),
        )
        self.assertEqual(self.prepare(raw)['replayed'], 1)
        self.assertEqual(self.task(claim.task_id), before_replay)
        self.assertEqual(before_replay['status'], 'needs_review')
        self.assertIsNone(self.tasks.claim())


if __name__ == '__main__':
    unittest.main()
