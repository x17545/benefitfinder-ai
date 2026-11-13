"""Official document bytes, complete generations and failure preservation."""

from contextlib import redirect_stdout
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from benefitfinder.application.collection import SourceResponse
from benefitfinder.application.official_documents import OfficialDocumentTarget
from benefitfinder.infrastructure.collection_files import read_collection_file
from benefitfinder.infrastructure.local_files import JsonFileSnapshotStore, JsonFileVersionStore
from benefitfinder.infrastructure.official_document_plans import official_document_plan_from_payload
from benefitfinder.ingestion.official_document_generations import load_official_document_generation
from benefitfinder.jobs.official_document_refresh import refresh_official_documents
from benefitfinder.jobs.daily_refresh import run_daily_refresh

AT = datetime(2026,11,13,tzinfo=timezone.utc)
TARGET = OfficialDocumentTarget('fictional-notice','youth:fictional:employment',
    'https://example.test/fictional-notice','html',categories=('employment',),
    required_text=('가상 청년 취업',))
HTML = b'<html><nav>ignored</nav><main>'+ '가상 청년 취업: 지원 조건'.encode()+b'</main></html>'


class OfficialDocumentsTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.fetcher = Mock()
        self.fetcher.fetch.return_value = SourceResponse(200,HTML,'text/html; charset=utf-8')
        self.at = AT
        self.pdf = Mock(return_value=['가상 청년 취업 PDF 조건'])

    def refresh(self, targets: tuple[OfficialDocumentTarget,...] = (TARGET,)) -> bool:
        self.at += timedelta(seconds=1)
        return refresh_official_documents(self.root,targets,fetcher=self.fetcher,
            snapshots=JsonFileSnapshotStore(self.root/'raw/f08-official-documents',private=True),
            versions_factory=JsonFileVersionStore,clock=lambda:self.at,
            pdf_pages=self.pdf,ingest_complete=None)

    def load(self):
        return load_official_document_generation(
            lambda ref,limit:read_collection_file(self.root,ref,limit),self.pdf)

    def status(self) -> dict:
        return json.loads((self.root/'processed/f08-official-documents/status.json').read_text())

    def test_original_body_exact_text_and_hash_survive_verified_replay(self) -> None:
        self.assertTrue(self.refresh())
        batch = self.load()
        record, = batch.records
        self.assertEqual(batch.source_name,'official_documents')
        self.assertEqual(record.fields['text'],'가상 청년 취업: 지원 조건\n')
        self.assertEqual(record.fields['text_status'],'ready')
        self.assertEqual((self.root/'raw/f08-official-documents'/record.snapshot_ref).read_bytes(),HTML)
        self.assertEqual(self.load().fingerprint(),batch.fingerprint())
        self.assertEqual((self.root/'raw/f08-official-documents'/record.snapshot_ref).stat().st_mode & 0o777,0o600)

    def test_unchanged_bytes_keep_material_hash_changed_body_changes_hash(self) -> None:
        self.refresh()
        original = self.load().records[0]
        self.assertTrue(self.refresh())
        self.assertEqual(self.load().records[0].record_sha256,original.record_sha256)
        self.assertEqual(self.status()['last_attempt']['changes'],{'unchanged':1})
        self.fetcher.fetch.return_value = SourceResponse(200,HTML.replace('지원 조건'.encode(),'변경 조건'.encode()),'text/html')
        self.assertTrue(self.refresh())
        self.assertNotEqual(self.load().records[0].record_sha256,original.record_sha256)
        self.assertEqual(self.status()['last_attempt']['changes'],{'changed':1})

    def test_transport_and_wrong_identity_do_not_replace_last_success(self) -> None:
        self.refresh()
        success = self.status()['last_success']
        fingerprint = self.load().fingerprint()
        for response in (SourceResponse(403,b'PRIVATE','text/html'),
                         SourceResponse(200,b'<main>login PRIVATE</main>','text/html')):
            self.fetcher.fetch.return_value = response
            self.assertFalse(self.refresh())
            self.assertEqual(self.status()['last_success'],success)
            self.assertEqual(self.load().fingerprint(),fingerprint)
            self.assertNotIn('PRIVATE',json.dumps(self.status()))

    def test_selected_complete_markup_ignores_outer_counter_but_detects_image_change(self) -> None:
        target = replace(TARGET,content_id='policyBody')
        def response(counter: int, image: str = 'v1') -> SourceResponse:
            return SourceResponse(200,(f'<main>조회수 {counter}<div id="policyBody">'
                f'<p>가상 청년 취업 조건</p><img src="{image}.png"></div></main>').encode(),'text/html')
        self.fetcher.fetch.return_value = response(1)
        self.assertTrue(self.refresh((target,)))
        original = self.load().records[0]
        self.fetcher.fetch.return_value = response(2)
        self.assertTrue(self.refresh((target,)))
        same = self.load().records[0]
        self.assertEqual(original.record_sha256,same.record_sha256)
        self.assertNotEqual(original.fields['raw_sha256'],same.fields['raw_sha256'])
        self.fetcher.fetch.return_value = response(3,'v2')
        self.assertTrue(self.refresh((target,)))
        changed = self.load().records[0]
        self.assertNotEqual(original.record_sha256,changed.record_sha256)
        self.assertEqual(original.fields['text_sha256'],changed.fields['text_sha256'])

    def test_missing_or_duplicate_selected_region_does_not_fall_back_to_whole_page(self) -> None:
        target = replace(TARGET,content_id='policyBody')
        for body in (HTML, b'<div id="policyBody">one</div><div id="policyBody">two</div>'):
            self.fetcher.fetch.return_value = SourceResponse(200,body,'text/html')
            self.assertFalse(self.refresh((target,)))
            self.assertIsNone(self.status()['last_success'])

    def test_404_is_verified_missing_and_return_is_a_new_observation(self) -> None:
        self.refresh()
        self.fetcher.fetch.return_value = SourceResponse(404,b'not found','text/html')
        self.assertTrue(self.refresh())
        self.assertEqual(self.load().records,())
        self.assertEqual(self.status()['last_attempt']['changes'],{'missing':1})
        self.fetcher.fetch.return_value = SourceResponse(200,HTML,'text/html')
        self.assertTrue(self.refresh())
        self.assertEqual(len(self.load().records),1)
        self.assertEqual(self.status()['last_attempt']['changes'],{'new':1})

    def test_partial_failure_never_promotes_a_partial_selected_generation(self) -> None:
        self.refresh()
        before = self.status()['last_success']
        second = replace(TARGET,external_id='second',url=TARGET.url+'/second')
        # Scope changes cannot be misreported as source disappearance.
        self.assertFalse(self.refresh((TARGET,second)))
        self.assertEqual(self.status()['last_success'],before)
        self.assertEqual(len(self.load().records),1)

    def test_every_selected_response_is_required_before_complete_success(self) -> None:
        second = replace(TARGET,external_id='second',url=TARGET.url+'/second')
        self.fetcher.fetch.side_effect = [SourceResponse(200,HTML,'text/html'),RuntimeError('PRIVATE')]
        self.assertFalse(self.refresh((TARGET,second)))
        self.assertIsNone(self.status()['last_success'])

    def test_scanned_or_unreadable_pdf_keeps_raw_change_for_recheck(self) -> None:
        target = replace(TARGET,format='pdf')
        self.fetcher.fetch.return_value = SourceResponse(200,b'%PDF-fictional-one','application/pdf')
        self.assertTrue(self.refresh((target,)))
        before = self.load().records[0]
        self.assertIn('=== PDF PAGE 1 ===',before.fields['text'])
        self.pdf.side_effect = ValueError('PRIVATE decoder exception')
        self.fetcher.fetch.return_value = SourceResponse(200,b'%PDF-fictional-two','application/pdf')
        self.assertTrue(self.refresh((target,)))
        changed = self.load().records[0]
        self.assertNotEqual(before.record_sha256,changed.record_sha256)
        self.assertEqual(changed.fields['text_status'],'requires_review')
        self.assertIsNone(changed.fields['text'])
        self.assertNotIn('PRIVATE',json.dumps(changed.fields))

    def test_modified_original_cannot_reimport(self) -> None:
        self.refresh()
        batch = self.load()
        raw = self.root/'raw/f08-official-documents'/batch.records[0].snapshot_ref
        raw.write_bytes(raw.read_bytes()+b'changed')
        with self.assertRaisesRegex(ValueError,'invalid_official_document_generation'):
            self.load()

    def test_plan_requires_explicit_scope_official_confirmation_and_identity(self) -> None:
        row = {'external_id':TARGET.external_id,'policy_id':TARGET.policy_id,'url':TARGET.url,
               'format':'html','method':'GET','form':{},'categories':['employment'],
               'official_source_confirmed':True,'required_text':['가상 청년 취업'],'content_id':None}
        self.assertEqual(official_document_plan_from_payload({'format_version':1,'documents':[row]}),(TARGET,))
        for update in ({'categories':['education']},{'official_source_confirmed':False},
                       {'required_text':[]},{'url':'https://example.test/?apiKey=PRIVATE'},
                       {'form':{'token':'PRIVATE'}}):
            with self.subTest(update=update),self.assertRaisesRegex(ValueError,'invalid_official_document_plan'):
                official_document_plan_from_payload({'format_version':1,'documents':[{**row,**update}]})

    def test_daily_document_stage_runs_before_preparation_and_reports_failure(self) -> None:
        calls, sent = [], []
        def documents() -> int:
            calls.append('documents')
            return 1
        def prepare() -> dict:
            self.assertIn('documents',calls)
            return {'targets':0,'registered':0,'replayed':0,'failed':0}
        self.assertEqual(run_daily_refresh(self.root/'daily',refresh_policies=lambda:0,
            refresh_rate=lambda:0,refresh_documents=documents,prepare_analysis=prepare,
            clock=lambda:AT,notify=sent.append),1)
        self.assertIn('공식 첨부 문서: 실패',sent[0])

    def test_cli_uses_only_configured_plan_without_api_or_model_credentials(self) -> None:
        from benefitfinder.jobs.__main__ import main
        with patch.dict(os.environ,{'OFFICIAL_DOCUMENT_PLAN':'fictional-plan'},clear=True), \
             patch('benefitfinder.jobs.__main__._refresh_youthcenter',return_value=0), \
             patch('benefitfinder.jobs.__main__.refresh_ecos_rate_command',return_value=0), \
             patch('benefitfinder.jobs.__main__.refresh_official_documents_command',return_value=0) as documents, \
             redirect_stdout(StringIO()):
            self.assertEqual(main(['refresh-data','--data-dir',str(self.root)]),0)
        documents.assert_called_once_with(self.root,Path('fictional-plan'))
