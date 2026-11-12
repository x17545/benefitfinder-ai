"""Fictional human decisions; no real approval, official API, DB or LLM calls."""

from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from io import StringIO
import json
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import unittest

from backend.tests.catalog_fixtures import release_data, source_text
from backend.tests.service_catalog_fixtures import service_release_data, service_source_text
from benefitfinder.application.f12_review import F12ReviewDecision
from benefitfinder.application.policy_facts import PolicyAmountCandidate, PolicyFactsDraft
from benefitfinder.application.policy_rules import (
    PolicyReference, RuleCandidate, RuleCitation, RuleDirection, RuleKind, RuleReviewBatch, SourceDocument,
)
from benefitfinder.infrastructure.f12_catalog_files import prepare_f12_release, write_f12_catalog
from benefitfinder.infrastructure.policy_catalog_records import record_hash
from benefitfinder.infrastructure.policy_rule_files import (
    write_f12_review_pair, write_f12_decisions, write_f12_approved_export,
)
from benefitfinder.ingestion.rule_review_workflow import prepare_snapshot
from benefitfinder.jobs.__main__ import main


def review_fixture(root: Path):
    data = release_data()
    source = data['benefit']['source']
    document = SourceDocument('fictional-notice', data['benefit']['id'], source['url'],
                              source['document_version'], datetime.fromisoformat(source['checked_at']),
                              source_text(), True)
    def citation(evidence):
        return RuleCitation(document.id, document.document_version, document.content_sha256,
                            evidence['quote'], evidence['location'])
    snapshot = prepare_snapshot(RuleReviewBatch((document,),
        (PolicyReference(document.policy_id, 'youth', ('housing',)),), ()), at=document.collected_at)
    pay = citation(data['payment_evidence'][0])
    facts = PolicyFactsDraft(document, (citation(data['benefit']['eligibility_conditions'][0]['evidence'][0]),),
                            (PolicyAmountCandidate(pay, (1_000_000,)),), (pay,))
    decisions = tuple(F12ReviewDecision(key, 'approve', 'fictional-human', '가상 원문·의미 검수',
                                        document.collected_at + timedelta(hours=1))
                      for key in ('eligibility:1', 'amount:1', 'timing:1'))
    write_f12_review_pair(root / 'pair', snapshot, facts)
    write_f12_decisions(root / 'decisions.json', snapshot, facts, decisions)
    write_f12_approved_export(root / 'approved.json', snapshot, facts, root / 'decisions.json')
    plan = {'format_version': 1, 'conditions': [{'item_id': 'eligibility:1', 'condition_id': 'age'}],
            'payment_items': ['amount:1', 'timing:1'], 'conflicts': [], 'reference_only': [],
            'explanations': [{'item_id': 'eligibility:1', 'citation_index': 0, 'id': 'age',
                              'title': '가상 나이 근거', 'topics': ['age']}]}
    for name, value in (('record', data), ('plan', plan)):
        (root / (name + '.json')).write_text(json.dumps(value, ensure_ascii=False))
    (root / 'source.txt').write_text(document.text)
    return data, plan, snapshot, facts, decisions


class F12CatalogFilesTest(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.data, self.plan, self.snapshot, self.facts, self.decisions = review_fixture(self.root)

    def prepare(self):
        return prepare_f12_release(pair_dir=self.root / 'pair', decisions_path=self.root / 'decisions.json',
            approved_path=self.root / 'approved.json', record_path=self.root / 'record.json',
            plan_path=self.root / 'plan.json', source_text=source_text())

    def save(self, name, value):
        (self.root / (name + '.json')).write_text(json.dumps(value, ensure_ascii=False))

    def test_explicit_rules_and_quotes_preserved_and_whole_provenance_affects_approval(self):
        record = self.prepare()
        self.assertEqual(record.conditions[0].maximum, 34)
        self.assertEqual(record.payments[0].month, 1)
        self.assertEqual(record.explanation_clauses[0].evidence.quote, self.facts.eligibility[0].quote)
        self.assertIsNotNone(record.f12_review)
        old_hash = record_hash(record)
        self.plan['explanations'][0]['title'] += ' 변경'
        self.save('plan', self.plan)
        self.assertNotEqual(record_hash(self.prepare()), old_hash)
        self.data['benefit']['reviewed'] = False
        self.save('record', self.data)
        self.assertFalse(self.prepare().benefit.reviewed)

    def test_forged_export_stale_latest_decision_or_wrong_source_are_rejected(self):
        original = json.loads((self.root / 'approved.json').read_text())
        changed = deepcopy(original)
        changed['items'][0]['approval']['reviewer'] = 'forged'
        self.save('approved', changed)
        with self.assertRaisesRegex(ValueError, 'f12_approved_export_mismatch'):
            self.prepare()
        changed = deepcopy(original)
        changed['format_version'] = True
        self.save('approved', changed)
        with self.assertRaisesRegex(ValueError, 'f12_approved_export_mismatch'):
            self.prepare()
        self.save('approved', original)
        rejection = replace(self.decisions[-1], decision='reject', reviewed_at=self.decisions[-1].reviewed_at + timedelta(minutes=1))
        write_f12_decisions(self.root / 'new-decisions.json', self.snapshot, self.facts, self.decisions + (rejection,))
        (self.root / 'decisions.json').write_bytes((self.root / 'new-decisions.json').read_bytes())
        with self.assertRaisesRegex(ValueError, 'f12_approved_export_mismatch'):
            self.prepare()

    def test_source_cannot_be_substituted(self):
        self.data['benefit']['source']['url'] = 'https://other.example/'
        for evidence in self.data['payment_evidence'] + self.data['benefit']['eligibility_conditions'][0]['evidence']:
            evidence['source']['url'] = 'https://other.example/'
        self.save('record', self.data)
        with self.assertRaisesRegex(ValueError, 'f12_catalog_source_mismatch'):
            self.prepare()

    def test_reviewed_policy_categories_cannot_be_reclassified(self):
        for categories in (['employment'], ['employment', 'housing']):
            self.data['benefit']['categories'] = categories
            self.save('record', self.data)
            with self.assertRaisesRegex(ValueError, 'f12_catalog_classification_mismatch'):
                self.prepare()

    def test_missing_mappings_wrong_amount_and_pending_candidates_do_not_become_reviewed(self):
        for mutate in (
            lambda p: p.update(conditions=[]),
            lambda p: p.update(payment_items=['amount:1']),
            lambda p: p.update(payment_items=['amount:1', 'timing:1', 'amount:1']),
            lambda p: p['explanations'][0].update(citation_index=1),
            lambda p: p['explanations'][0].update(item_id='rule:99'),
        ):
            plan = deepcopy(self.plan)
            mutate(plan)
            self.save('plan', plan)
            with self.assertRaises(ValueError):
                self.prepare()
        self.save('plan', self.plan)
        self.data['benefit']['benefit_amount']['amount_won'] = 999
        self.data['benefit']['benefit_amount']['max_total_won'] = 999
        self.data['payments'][0]['amount_won'] = 999
        self.save('record', self.data)
        with self.assertRaisesRegex(ValueError, 'f12_amount_interpretation_mismatch'):
            self.prepare()

    def test_unreviewed_timing_cannot_disappear_into_a_verified_payment(self):
        write_f12_decisions(self.root / 'pending-decisions.json', self.snapshot, self.facts, self.decisions[:-1])
        (self.root / 'decisions.json').write_bytes((self.root / 'pending-decisions.json').read_bytes())
        write_f12_approved_export(self.root / 'pending-export.json', self.snapshot, self.facts, self.root / 'decisions.json')
        (self.root / 'approved.json').write_bytes((self.root / 'pending-export.json').read_bytes())
        self.plan['payment_items'] = ['amount:1']
        self.save('plan', self.plan)
        with self.assertRaises(ValueError):
            self.prepare()

    def add_rule(self, *, conditional=False):
        document = self.facts.document
        quote = '두 정책은 중복 불가입니다.'
        rule = RuleCandidate('fictional-conflict', document.policy_id, ('youth:catalog:B',),
            RuleKind.SIMULTANEOUS_EXCLUSION, quote, (RuleCitation(document.id, document.document_version,
            document.content_sha256, quote, '가상 중복 제한'),), direction=RuleDirection.BOTH,
            conditions=('추가 조건을 확인한 경우',) if conditional else ())
        batch = replace(self.snapshot.batch, candidates=(rule,), policies=self.snapshot.batch.policies +
                        (PolicyReference('youth:catalog:B', 'youth', ('housing',)),))
        snapshot = prepare_snapshot(batch, at=self.snapshot.created_at)
        decisions = self.decisions + (F12ReviewDecision('rule:1', 'approve', 'fictional-human',
            '가상 중복 문맥 검수', self.decisions[-1].reviewed_at + timedelta(minutes=1)),)
        write_f12_review_pair(self.root / 'rule-pair', snapshot, self.facts)
        # Reuse this test's paths without editing a stored original pair.
        self.root.joinpath('pair').rename(self.root / 'original-pair')
        self.root.joinpath('rule-pair').rename(self.root / 'pair')
        write_f12_decisions(self.root / 'rule-decisions.json', snapshot, self.facts, decisions)
        (self.root / 'decisions.json').write_bytes((self.root / 'rule-decisions.json').read_bytes())
        write_f12_approved_export(self.root / 'rule-export.json', snapshot, self.facts, self.root / 'decisions.json')
        (self.root / 'approved.json').write_bytes((self.root / 'rule-export.json').read_bytes())
        self.data['benefit']['conflicts'] = [{'policy_id': 'youth:catalog:B', 'evidence': [{
            'quote': quote, 'location': '가상 중복 제한', 'source': self.data['benefit']['source']}]}]
        self.plan['conflicts'] = [{'item_id': 'rule:1', 'policy_id': 'youth:catalog:B'}]
        self.save('record', self.data)
        self.save('plan', self.plan)

    def test_simple_bilateral_exclusion_maps_but_conditions_cannot_be_flattened(self):
        self.add_rule(conditional=True)
        with self.assertRaisesRegex(ValueError, 'f12_rule_requires_manual_resolution'):
            self.prepare()
        self.data['benefit']['conflicts'] = []
        self.plan['conflicts'] = []
        self.plan['reference_only'] = [{'item_id': 'rule:1', 'reason': '가상 복합 조건: 코드 계산 미지원'}]
        self.save('record', self.data)
        self.save('plan', self.plan)
        with self.assertRaisesRegex(ValueError, 'f12_unresolved_candidates_require_manual_review'):
            self.prepare()
        self.data['benefit']['reviewed'] = False
        self.data['unresolved_combination_rules'] = True
        self.save('record', self.data)
        self.assertFalse(self.prepare().benefit.reviewed)

    def test_simple_bilateral_rule_is_preserved(self):
        self.add_rule()
        self.assertEqual(self.prepare().benefit.conflicts[0].policy_id, 'youth:catalog:B')
        forged = deepcopy(self.data['benefit']['conflicts'][0])
        forged['evidence'][0]['quote'] = '연 소득 3천만 원 이하.'
        self.data['benefit']['conflicts'].insert(0, forged)
        self.save('record', self.data)
        with self.assertRaisesRegex(ValueError, 'duplicate_conflict'):
            self.prepare()

    def test_service_requires_explicit_conditions_and_keeps_price_unknown(self):
        data = service_release_data(price=False)
        data['benefit']['conflicts'] = []
        source = data['benefit']['source']
        document = SourceDocument('fictional-service', data['benefit']['id'], source['url'],
            source['document_version'], datetime.fromisoformat(source['checked_at']), service_source_text(), True)
        cite = data['benefit']['eligibility_conditions'][0]['evidence'][0]
        facts = PolicyFactsDraft(document, (RuleCitation(document.id, document.document_version,
            document.content_sha256, cite['quote'], cite['location']),), (), ())
        snapshot = prepare_snapshot(RuleReviewBatch((document,),
            (PolicyReference(document.policy_id, 'youth', ('employment', 'housing')),), ()), at=document.collected_at)
        decision = F12ReviewDecision('eligibility:1', 'approve', 'fictional-human', '가상 서비스 검수',
                                     document.collected_at + timedelta(hours=1))
        write_f12_review_pair(self.root / 'service-pair', snapshot, facts)
        write_f12_decisions(self.root / 'service-decisions.json', snapshot, facts, (decision,))
        write_f12_approved_export(self.root / 'service-export.json', snapshot, facts, self.root / 'service-decisions.json')
        self.save('record', data)
        self.plan['payment_items'] = []
        self.save('plan', self.plan)
        record = prepare_f12_release(pair_dir=self.root / 'service-pair',
            decisions_path=self.root / 'service-decisions.json', approved_path=self.root / 'service-export.json',
            record_path=self.root / 'record.json', plan_path=self.root / 'plan.json', source_text=document.text)
        self.assertEqual(record.conditions[0].maximum, 39)
        self.assertIsNone(record.price)
        self.assertIsNotNone(record.f12_review)

    def test_cli_writes_private_create_only_output_and_never_an_approval(self):
        args = ['catalog-from-f12', '--pair-dir', str(self.root / 'pair')]
        for flag, name in (('decisions', 'decisions.json'), ('approved', 'approved.json'),
                           ('input', 'record.json'), ('plan', 'plan.json'), ('source', 'source.txt'),
                           ('output', 'prepared.json')):
            args.extend(['--' + flag, str(self.root / name)])
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(main(args), 0)
        self.assertTrue(json.loads(out.getvalue())['requires_publication_approval'])
        self.assertEqual(stat.S_IMODE((self.root / 'prepared.json').stat().st_mode), 0o600)
        with self.assertRaises(ValueError):
            write_f12_catalog(self.root / 'prepared.json', self.prepare())
        with redirect_stdout(StringIO()), redirect_stderr(err):
            self.assertEqual(main(args), 2)
        self.assertNotIn(source_text(), err.getvalue())
        self.assertFalse((self.root / 'approval.json').exists())
