"""Exact monitored supplements, explicit replacement IDs and closed failure paths."""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import hashlib
import unittest
from unittest.mock import Mock

from backend.tests.test_policy_preparation import AT, material_hash, plan_data, source
from benefitfinder.application.collection import SourceResponse
from benefitfinder.application.official_documents import OfficialDocumentTarget
from benefitfinder.application.prepare_policy_analysis import PreparationSource, prepare_analysis_inputs
from benefitfinder.infrastructure.policy_preparation_plans import preparation_plan_from_payload
from benefitfinder.ingestion.official_documents import document_fields, official_document_material_hash


def supplemented_plan(*keys: str) -> dict:
    raw = plan_data(*keys)
    raw['targets'][0]['official_documents'] = [
        {'external_id': 'fictional-notice', 'document_id': 'official:existing-manual-document'}]
    return raw


def official_source(**updates) -> PreparationSource:
    target = OfficialDocumentTarget('fictional-notice', 'youth:fictional:A',
        'https://example.test/official-notice', 'html', categories=('employment',),
        required_text=('가상 공식',))
    fields = document_fields(target, SourceResponse(200,
        '<main><h1>가상 공식 공고</h1><p>지원 대상은 취업 준비 청년입니다.</p></main>'.encode(),
        'text/html; charset=utf-8'), lambda _: ())
    fields.update(updates)
    return PreparationSource(target.external_id, official_document_material_hash(fields),
                             AT + timedelta(seconds=30), fields)


class OfficialDocumentPreparationTest(unittest.TestCase):
    def prepare(self, raw=None, current=None):
        raw = supplemented_plan() if raw is None else raw
        api = Mock()
        api.load.return_value = {key: source(key) for key in ('A', 'B')}
        official = Mock()
        official.load.return_value = {'fictional-notice': official_source()} if current is None else current
        registered = []

        def register(value):
            registered.append(value)
            return {'replayed': False}

        counts = prepare_analysis_inputs(preparation_plan_from_payload(raw), api, register, material_hash,
            official_sources=official, official_material_hash=official_document_material_hash)
        return counts, registered, official

    def test_exact_text_and_source_times_are_preserved_with_explicit_existing_id(self):
        counts, (value,), store = self.prepare()
        self.assertEqual(counts, {'targets': 1, 'registered': 1, 'replayed': 0, 'failed': 0})
        api, official = value.documents
        self.assertEqual(api.collected_at, AT)
        self.assertEqual(official.collected_at, AT + timedelta(seconds=30))
        self.assertEqual(official.id, 'official:existing-manual-document')
        self.assertEqual(official.text, official_source().fields['text'])
        self.assertEqual(official.document_version, 'official-v1:' + official_source().material_sha256)
        self.assertEqual({item.source for item in value.collection_sources}, {'youthcenter', 'official_documents'})
        self.assertEqual({item.document_id for item in value.document_sources}, {api.id, official.id})
        store.load.assert_called_once_with(('fictional-notice',))

    def test_missing_unreadable_mismatched_or_tampered_supplement_never_partially_registers(self):
        invalid = [{}, {'fictional-notice': replace(official_source(), material_sha256='a' * 64)}]
        for changes in ({'policy_id': 'youth:fictional:OTHER'}, {'categories': ['housing']},
                        {'text_status': 'requires_review', 'text': None, 'text_sha256': None},
                        {'text_sha256': 'a' * 64}, {'source_url': 'http://example.test/not-official'},
                        {'text': '가' * 100_001, 'text_sha256': hashlib.sha256(('가' * 100_001).encode()).hexdigest()}):
            invalid.append({'fictional-notice': official_source(**changes)})
        for current in invalid:
            with self.subTest(fields=list(current)):
                counts, values, _ = self.prepare(current=current)
                self.assertEqual(counts['failed'], 1)
                self.assertEqual(values, [])

    def test_independent_api_only_target_still_succeeds(self):
        counts, values, _ = self.prepare(supplemented_plan('A', 'B'), {})
        self.assertEqual(counts, {'targets': 2, 'registered': 1, 'replayed': 0, 'failed': 1})
        self.assertEqual(values[0].policy_id, 'youth:fictional:B')
        self.assertEqual(values[0].document_sources, ())

    def test_legacy_plan_does_not_read_official_sources_or_change_payload(self):
        counts, (value,), store = self.prepare(plan_data())
        self.assertEqual(counts['registered'], 1)
        store.load.assert_not_called()
        self.assertNotIn('document_sources', value.payload())
        self.assertEqual(value.fingerprint(), replace(value, document_sources=()).fingerprint())

    def test_supplement_plan_rejects_null_unknown_duplicate_and_excess_documents(self):
        original = supplemented_plan()
        variants = [None, [{'external_id': 'fictional-notice', 'document_id': 'x', 'approved': True}],
                    original['targets'][0]['official_documents'] * 2,
                    [{'external_id': 'fictional-' + str(index), 'document_id': 'x' + str(index)}
                     for index in range(4)],
                    [{'external_id': 'fictional-notice', 'document_id': 'bad\nID'}]]
        for documents in variants:
            raw = deepcopy(original)
            raw['targets'][0]['official_documents'] = documents
            with self.subTest(documents=documents), self.assertRaisesRegex(ValueError, '^invalid_f12_preparation_plan$'):
                preparation_plan_from_payload(raw)


if __name__ == '__main__':
    unittest.main()
