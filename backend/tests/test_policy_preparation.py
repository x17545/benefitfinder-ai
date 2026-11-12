"""Explicit API projection, scope checks and private failure contracts."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from benefitfinder.application.prepare_policy_analysis import PreparationSource, prepare_analysis_inputs
from benefitfinder.infrastructure.policy_preparation_plans import (
    load_preparation_plan, preparation_plan_from_payload,
)

AT = datetime(2026, 11, 2, tzinfo=timezone.utc)


def fields(key: str = 'A', *, body: str = '가상: 만 19세 이상 청년에게 20만 원을 지원합니다.') -> dict:
    return {'plcyNo': key, 'plcyNm': '가상 청년 취업 '+key,
            'category': 'fictional-employment', 'ageScope': 'fictional-youth',
            'support': body, 'timing': None, 'unselectedNote': 'PRIVATE source not selected'}


def material_hash(data: dict) -> str:
    return hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False,
                                    separators=(',',':')).encode()).hexdigest()


def plan_data(*keys: str) -> dict:
    keys = keys or ('A',)
    return {'format_version':1,
        'policies':[{'id':'youth:fictional:'+key,'name':'가상 청년 취업 '+key,
                     'categories':['employment'],'aliases':[]} for key in keys],
        'targets':[{'policy_id':'youth:fictional:'+key,'documents':[{
            'external_id':key,'name_field':'plcyNm','body_fields':['support','timing'],
            'scope_guards':{'category':'fictional-employment','ageScope':'fictional-youth'},
        }]} for key in keys]}


def source(key: str = 'A', **kwargs) -> PreparationSource:
    data = fields(key, **kwargs)
    return PreparationSource(key,material_hash(data),AT,data)


class PolicyPreparationTest(unittest.TestCase):
    def prepare(self, raw: dict | None = None, current: dict | None = None):
        plan = preparation_plan_from_payload(raw if raw is not None else plan_data())
        store = Mock()
        store.load.return_value = current if current is not None else {'A':source()}
        registered = []
        def register(value):
            registered.append(value)
            return {'replayed':False}
        counts = prepare_analysis_inputs(plan,store,register,material_hash)
        return counts, registered

    def test_exact_projection_preserves_quotes_without_unselected_fields_or_missing_inventions(self):
        counts, (value,) = self.prepare()
        self.assertEqual(counts,{'targets':1,'registered':1,'replayed':0,'failed':0})
        doc, = value.documents
        self.assertEqual(doc.text,'[plcyNm]\n가상 청년 취업 A\n\n[support]\n'+fields()['support'])
        self.assertNotIn('PRIVATE',doc.text)
        self.assertNotIn('timing',doc.text)
        self.assertNotIn('원문에 없음',doc.text)
        self.assertTrue(doc.official)
        self.assertIn('plcyNo=A',doc.source_url)
        self.assertNotIn('apiKey',doc.source_url)
        self.assertEqual(doc.collected_at,AT)
        self.assertRegex(doc.document_version,r'^api-v1:[0-9a-f]{64}$')
        self.assertEqual(value.collection_sources[0].material_sha256,source().material_sha256)

    def test_changed_projection_has_new_version_without_inventing_new_collection_time(self):
        _, (before,) = self.prepare()
        raw = plan_data()
        raw['targets'][0]['documents'][0]['body_fields'] = ['timing','support']
        _, (same,) = self.prepare(raw)
        self.assertEqual(before.fingerprint(),same.fingerprint())
        raw['targets'][0]['documents'][0]['body_fields'] = ['support','extraBody']
        data = fields()
        data['extraBody'] = '가상 공식 보충 문장'
        _, (changed,) = self.prepare(raw, {'A':PreparationSource('A',material_hash(data),AT,data)})
        self.assertNotEqual(before.documents[0].document_version,changed.documents[0].document_version)
        self.assertEqual(changed.documents[0].collected_at,AT)

    def test_scope_name_body_integrity_and_data_type_failures_register_nothing(self):
        for updates in ({'category':'education'},{'plcyNm':'다른 가상 정책'}, {'support':None},
                        {'support':True},{'support':{'hidden':'nested'}},{'support':'bad\0text'}):
            with self.subTest(updates=updates):
                data = {**fields(),**updates}
                counts, registered = self.prepare(current={'A':PreparationSource('A',material_hash(data),AT,data)})
                self.assertEqual(counts['failed'],1)
                self.assertEqual(registered,[])
        for current in ({}, {'A':replace(source(),material_sha256='a'*64)},
                        {'A':replace(source(),accepted_at=AT.replace(tzinfo=None))}):
            counts, registered = self.prepare(current=current)
            self.assertEqual(counts['failed'],1)
            self.assertEqual(registered,[])

    def test_independent_targets_keep_success_and_registration_replay_is_distinct(self):
        plan = preparation_plan_from_payload(plan_data('A','B'))
        store, register = Mock(), Mock(return_value={'replayed':True})
        store.load.return_value = {'A':source()}
        self.assertEqual(prepare_analysis_inputs(plan,store,register,material_hash),
                         {'targets':2,'registered':0,'replayed':1,'failed':1})
        register.assert_called_once()
        register.side_effect = RuntimeError('PRIVATE original key')
        self.assertEqual(prepare_analysis_inputs(plan,store,register,material_hash)['failed'],2)

    def test_lookup_failure_is_not_an_empty_success(self):
        store = Mock()
        store.load.side_effect = ValueError('PRIVATE SQL')
        register = Mock()
        with self.assertRaises(ValueError):
            prepare_analysis_inputs(preparation_plan_from_payload(plan_data()),store,register,material_hash)
        register.assert_not_called()

    def test_api_fields_accept_exact_zero_without_fabricating_currency(self):
        data = fields()
        data['support'] = 0
        _, (value,) = self.prepare(current={'A':PreparationSource('A',material_hash(data),AT,data)})
        self.assertIn('[support]\n0',value.documents[0].text)
        self.assertNotIn('0원',value.documents[0].text)

    def test_plan_rejects_sensitive_fields_unknown_scope_and_approval(self):
        bad = []
        for field in ('apiKeyNm','secret','operInstPicNm','contactTelno','parentIncome',
                      'profile','inqCnt','support.text'):
            raw = plan_data()
            raw['targets'][0]['documents'][0]['body_fields'] = [field]
            bad.append(raw)
        for change in ({'reviewed':True},{'profile':{}},{'format_version':True}):
            bad.append({**plan_data(),**change})
        for value in ('education','living'):
            raw = plan_data()
            raw['policies'][0]['categories'] = [value]
            bad.append(raw)
        for raw in bad:
            with self.assertRaisesRegex(ValueError,'^invalid_f12_preparation_plan$'):
                preparation_plan_from_payload(raw)

    def test_plan_rejects_duplicate_targets_sources_keys_and_unbounded_inputs(self):
        bad = []
        for key in ('policies','targets'):
            raw = plan_data()
            raw[key] *= 2
            bad.append(raw)
        for change in ({'scope_guards':{}},{'body_fields':['support','support']},
                       {'body_fields':['plcyNm']},{'external_id':'A\nB'}):
            raw = plan_data()
            raw['targets'][0]['documents'][0].update(change)
            bad.append(raw)
        raw = plan_data()
        raw['targets'][0]['documents'] *= 2
        bad.append(raw)
        for raw in bad:
            with self.assertRaises(ValueError):
                preparation_plan_from_payload(raw)
        with TemporaryDirectory() as temporary:
            path = Path(temporary)/'plan.json'
            path.write_text(json.dumps(plan_data()))
            self.assertEqual(len(load_preparation_plan(path).targets),1)
            for data in ('{"format_version":1,"format_version":1}','{"x":NaN}', ' '*262145):
                path.write_text(data)
                with self.assertRaisesRegex(ValueError,'^invalid_f12_preparation_plan$'):
                    load_preparation_plan(path)

    def test_no_quiet_document_truncation(self):
        counts, registered = self.prepare(current={'A':source(body='가'*100_001)})
        self.assertEqual(counts['failed'],1)
        self.assertEqual(registered,[])

    def test_confirmed_alias_and_multiple_api_documents_keep_separate_bindings(self):
        raw = plan_data()
        raw['policies'][0]['aliases'] = ['가상 청년 취업 B']
        second = deepcopy(raw['targets'][0]['documents'][0])
        second['external_id'] = 'B'
        raw['targets'][0]['documents'].append(second)
        counts, (value,) = self.prepare(raw,{'A':source(),'B':source('B')})
        self.assertEqual(counts['registered'],1)
        self.assertEqual(len(value.documents),2)
        self.assertNotEqual(value.documents[0].id,value.documents[1].id)
        self.assertEqual({item.external_id for item in value.collection_sources},{'A','B'})
