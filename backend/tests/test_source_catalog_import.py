"""Fictional inventory and injected transactions; no actual DB, model or env."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

from benefitfinder.application.decider import Compilation
from benefitfinder.application.policy_analysis import AnalysisPolicy, AnalysisSource, PolicyAnalysisInput
from benefitfinder.application.policy_rules import SourceDocument
from benefitfinder.infrastructure.database.repositories.source_catalog_import import (
    load_source_inventory, load_source_release_identities,
)
from benefitfinder.infrastructure.policy_catalog_records import CatalogRecord, record_hash
from benefitfinder.infrastructure.unreviewed_catalog import validate_unreviewed
from benefitfinder.ingestion.decider_pending import compile_pending_source
from benefitfinder.jobs.source_catalog_import import run_source_catalog_import


ROOT = Path('/virtual/source-import')
INVENTORY = {'source_counts': {'youthcenter': {'total': 4029, 'active': 4029, 'missing': 0}}}


def value(number: str) -> PolicyAnalysisInput:
    policy_id = 'youth:' + number
    fields = {
        'plcyNo': number, 'plcyNm': '[가상] 주거 정책 ' + number, 'lclsfNm': '주거',
        'plcySprtCn': '월 최대 20만원. 실제 비용과 기존 수급액에 따라 차감한다.',
        'aplyPrdSeCd': '0057002', 'aplyYmd': '상시',
        'rgtrInstCdNm': '가상 주거 기관', 'jobCd': '9999999',
        'earnEtcCn': '소득 조건은 별도 안내 확인.',
    }
    document = SourceDocument(
        'youthcenter:' + number, policy_id,
        'https://www.youthcenter.go.kr/youthPolicy/ythPlcyTotalSearch/ythPlcyDetail/' + number,
        'sha256:' + 'a' * 64, datetime(2026, 11, 16, tzinfo=timezone.utc),
        '\n\n'.join('[' + name + ']\n' + text for name, text in fields.items()), True,
    )
    return PolicyAnalysisInput(policy_id, (document,),
        (AnalysisPolicy(policy_id, fields['plcyNm'], ('housing',)),),
        (AnalysisSource('youthcenter', number, 'a' * 64),))


def validate(payload: dict, source: str) -> tuple[CatalogRecord, str]:
    record = CatalogRecord.model_validate(payload)
    return record, validate_unreviewed(record, source)


def identity(record: CatalogRecord, **changes) -> dict:
    result = {
        'policy_id': record.benefit.id,
        'document_version': record.benefit.source.document_version,
        'record_sha256': record_hash(record),
        'publication_status': 'published', 'review_type': 'unreviewed',
        'review_status': 'unreviewed', 'source': 'youthcenter',
        'external_id': record.collection_sources[0].external_id,
    }
    result.update(changes)
    return result


class MemoryFiles:
    def __init__(self) -> None:
        self.files = {}
        self.crash_after_commit = False

    def save(self, path: Path, content: object) -> None:
        if (self.crash_after_commit and path.name == 'checkpoint.json'
                and content.get('status') == 'loaded_unreviewed'):
            self.crash_after_commit = False
            raise OSError('fictional checkpoint interruption')
        self.files[path] = deepcopy(content)

    def load(self, path: Path) -> dict | None:
        return deepcopy(self.files.get(path))


class MemoryDatabase:
    def __init__(self) -> None:
        self.identities = []
        self.calls = []
        self.lookups = []
        self.bad = set()
        self.commit_then_fail = False

    def existing(self, ids: tuple[str, ...], external_ids: tuple[str, ...]) -> tuple[dict, ...]:
        self.lookups.append((ids, external_ids))
        return tuple(deepcopy(self.identities))

    def publish(self, entries: list[tuple[CatalogRecord, str]]) -> None:
        ids = [record.benefit.id for record, _ in entries]
        self.calls.append(ids)
        if set(ids) & self.bad:
            raise ValueError('catalog_source_is_stale')  # Entire virtual transaction rolls back.
        self.identities.extend(identity(record) for record, _ in entries)
        if self.commit_then_fail:
            self.commit_then_fail = False
            raise RuntimeError('private connection credentials must not be included')


class SourceCatalogImportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.files = MemoryFiles()
        self.db = MemoryDatabase()

    def run_import(self, values: tuple[PolicyAnalysisInput, ...], *, publish: bool = False,
                   compile_source=compile_pending_source, validator=validate, selection=None) -> dict:
        selection = selection or {'counts': {
            'targets': len({v.policy_id for v in values}), 'held': 0, 'out_of_scope': 4029 - len(values),
        }, 'reason_counts': {}}
        return run_source_catalog_import(
            values, root=ROOT, inventory=INVENTORY, selection=selection,
            compile_source=compile_source, validate=validator, load_existing=self.db.existing,
            save=self.files.save, load=self.files.load,
            publish=self.db.publish if publish else None,
        )

    def test_dry_run_unique_counts_inventory_and_literal_unknown_amount(self) -> None:
        first = value('1')
        report = self.run_import((first, first, value('2')))
        self.assertEqual(report['counts']['source_active'], 4029)
        self.assertEqual(report['counts']['selected'], 2)
        self.assertEqual(report['counts']['prepared'], 2)
        self.assertEqual(report['counts']['duplicate_inputs'], 1)
        self.assertEqual(report['counts']['loaded_unreviewed'], 0)
        self.assertEqual(report['counts']['calculation_published'], 0)
        self.assertEqual(report['model_analysis'], 'not_run')
        self.assertFalse(self.db.calls)
        checkpoints = [v for p, v in self.files.files.items() if p.name == 'checkpoint.json']
        self.assertEqual(len(checkpoints), 2)
        for saved in checkpoints:
            record = saved['payload']
            amount = record['benefit']['benefit_amount']
            self.assertIsNone(amount['amount_won'])
            self.assertIsNone(amount['max_total_won'])
            self.assertEqual(amount['amount_status'], 'unknown')
            self.assertIn('최대 20만원', amount['amount_text'])
            self.assertIsNone(record['payments'])
            self.assertFalse(record['benefit']['reviewed'])
            self.assertEqual(record['benefit']['application']['status'], 'unknown')

    def test_publication_uses_ten_record_transactions_and_counts_only_new_unreviewed(self) -> None:
        report = self.run_import(tuple(value(str(i)) for i in range(23)), publish=True)
        self.assertEqual([len(group) for group in self.db.calls], [10, 10, 3])
        self.assertEqual(report['counts']['loaded_unreviewed'], 23)
        self.assertEqual(report['counts']['prepared'], 0)
        self.assertEqual(report['counts']['calculation_published'], 0)
        self.assertEqual(len({row['policy_id'] for row in self.db.identities}), 23)

    def test_human_ai_unreviewed_and_retired_history_are_never_overwritten_or_restored(self) -> None:
        for review_type in ('human', 'ai', 'unreviewed'):
            for status in ('published', 'retired'):
                with self.subTest(review_type=review_type, status=status):
                    self.setUp()
                    item = value('1')
                    record, _ = validate(compile_pending_source(item).payload, item.documents[0].text)
                    self.db.identities.append(identity(record, review_type=review_type,
                        review_status='unreviewed' if review_type == 'unreviewed' else 'approved',
                        publication_status=status))
                    original = deepcopy(self.db.identities)
                    report = self.run_import((item,), publish=True)
                    expected = 'replayed' if (review_type, status) == ('unreviewed', 'published') else 'protected_existing'
                    self.assertEqual(report['counts'][expected], 1)
                    self.assertEqual(report['counts']['loaded_unreviewed'], 0)
                    self.assertFalse(self.db.calls)
                    self.assertEqual(self.db.identities, original)

    def test_different_digest_version_and_other_alias_are_protected(self) -> None:
        item = value('1')
        record, _ = validate(compile_pending_source(item).payload, item.documents[0].text)
        for changes in ({'record_sha256': 'b' * 64}, {'document_version': 'old-version'},
                        {'policy_id': 'youth:youthcenter:1'}, {'policy_id': 'youth:other:1'}):
            with self.subTest(changes=changes):
                self.setUp()
                self.db.identities.append(identity(record, **changes))
                report = self.run_import((item,), publish=True)
                self.assertEqual(report['counts']['protected_existing'], 1)
                self.assertFalse(self.db.calls)

    def test_failed_batch_is_isolated_and_retry_counts_remain_unique(self) -> None:
        values = tuple(value(str(i)) for i in range(12))
        self.db.bad.add('youth:3')
        first = self.run_import(values, publish=True)
        self.assertEqual(first['counts']['loaded_unreviewed'], 11)
        self.assertEqual(first['counts']['failed'], 1)
        failed = next(e for e in first['entries'] if e['policy_id'] == 'youth:3')
        self.assertEqual(failed['reasons'], ['catalog_source_is_stale'])
        self.assertEqual(failed['batch_error']['code'], 'catalog_source_is_stale')
        self.assertEqual(failed['attempts'], 2)
        self.db.bad.clear()
        second = self.run_import(values, publish=True)
        self.assertEqual(second['counts']['replayed'], 11)
        self.assertEqual(second['counts']['loaded_unreviewed'], 1)
        self.assertEqual(second['counts']['failed'], 0)
        retried = next(e for e in second['entries'] if e['policy_id'] == 'youth:3')
        self.assertEqual(retried['attempts'], 3)
        self.assertEqual(len(self.db.identities), 12)

    def test_db_replay_works_after_committed_write_and_lost_checkpoint(self) -> None:
        values = (value('1'), value('2'))
        self.files.crash_after_commit = True
        with self.assertRaises(OSError):
            self.run_import(values, publish=True)
        self.files.files.clear()  # Also reproduce total loss of local state.
        report = self.run_import(values, publish=True)
        self.assertEqual(report['counts']['replayed'], 2)
        self.assertEqual(report['counts']['loaded_unreviewed'], 0)
        self.assertEqual(len(self.db.calls), 1)
        self.assertEqual(len(self.db.identities), 2)

    def test_lost_commit_response_is_reconciled_without_duplicate_insert(self) -> None:
        self.db.commit_then_fail = True
        report = self.run_import((value('1'), value('2')), publish=True)
        self.assertEqual(report['counts']['replayed'], 2)
        self.assertEqual(report['counts']['failed'], 0)
        self.assertEqual(len(self.db.calls), 1)
        self.assertNotIn('credentials', json.dumps(report))

    def test_checkpoint_status_does_not_authorize_skipping_database_requery(self) -> None:
        values = (value('1'),)
        self.run_import(values, publish=True)
        self.db.identities.clear()
        report = self.run_import(values, publish=True)
        self.assertEqual(report['counts']['loaded_unreviewed'], 1)
        self.assertEqual(report['counts']['replayed'], 0)
        self.assertEqual(len(self.db.calls), 2)

    def test_changed_source_or_record_has_a_different_checkpoint(self) -> None:
        original = value('1')
        self.run_import((original,))
        document = replace(original.documents[0], text=original.documents[0].text + '\n\n[etcMttrCn]\n추가 조건')
        changed = replace(original, documents=(document,))
        self.run_import((changed,))
        checkpoints = [p for p in self.files.files if p.name == 'checkpoint.json']
        self.assertEqual(len(checkpoints), 2)

    def test_selection_holds_and_preparation_failure_are_separate_from_loaded_count(self) -> None:
        selection = {'counts': {'targets': 3, 'selected': 1, 'held': 2, 'out_of_scope': 4026},
                     'reason_counts': {'mixed_official_category': 2}}
        report = self.run_import((value('1'),), publish=True, selection=selection,
            compile_source=lambda _: Compilation(None, ('pending_service_notice',), {}))
        self.assertEqual(report['counts']['held'], 3)
        self.assertEqual(report['counts']['loaded_unreviewed'], 0)
        self.assertEqual(report['reason_counts'], {'mixed_official_category': 2, 'pending_service_notice': 1})
        self.assertFalse(self.db.calls)

    def test_lookup_failure_prevents_writing_and_retains_a_safe_failure_reason(self) -> None:
        def unavailable(*args):
            raise RuntimeError('postgresql://private:secret@private.invalid')
        self.db.existing = unavailable
        report = self.run_import((value('1'),), publish=True)
        self.assertEqual(report['counts']['failed'], 1)
        self.assertEqual(report['entries'][0]['reasons'], ['source_import_existing_lookup_failed'])
        self.assertNotIn('secret', json.dumps(report))
        self.assertFalse(self.db.calls)

    def test_model_modules_are_not_imported_by_the_source_only_api(self) -> None:
        script = r'''
import importlib.abc
import sys
class NoModel(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith('benefitfinder.ai') or fullname in {
            'benefitfinder.infrastructure.decider_client',
            'benefitfinder.application.decider_evaluation',
            'torch', 'transformers',
        }:
            raise AssertionError('model import: ' + fullname)
sys.meta_path.insert(0, NoModel())
from backend.tests.test_source_catalog_import import SourceCatalogImportTest, value
test = SourceCatalogImportTest()
test.setUp()
report = test.run_import((value('1'),), publish=True)
assert report['model_analysis'] == 'not_run'
assert report['counts']['loaded_unreviewed'] == 1
'''
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)


class SourceImportRepositoryTest(unittest.TestCase):
    def test_inventory_counts_all_sources_and_active_categories_without_mvp_filter(self) -> None:
        connection = MagicMock()
        connection.execute.return_value.fetchall.side_effect = [
            [{'source': 'youthcenter', 'total': 4040, 'active': 4029},
             {'source': 'gov24', 'total': 7, 'active': 6}],
            [{'category': '주거', 'subcategory': '주거비', 'count': 900},
             {'category': '교육', 'subcategory': '교육비', 'count': 300}],
        ]
        with patch('benefitfinder.infrastructure.database.repositories.source_catalog_import.psycopg.connect') as connect:
            connect.return_value.__enter__.return_value = connection
            result = load_source_inventory('postgresql://virtual')
        self.assertEqual(result['source_counts']['youthcenter'], {'total': 4040, 'active': 4029, 'missing': 11})
        self.assertEqual(result['source_counts']['gov24']['active'], 6)
        self.assertEqual(result['category_counts'][1]['category'], '교육')
        self.assertIn('default_transaction_read_only=on', connect.call_args.kwargs['options'])

    def test_release_lookup_reads_fresh_published_and_retired_identities_every_time(self) -> None:
        connection = MagicMock()
        connection.execute.return_value.fetchall.side_effect = [[], [{
            'policy_id': 'youth:1', 'document_version': 'v1', 'record_sha256': 'a' * 64,
            'publication_status': 'retired', 'review_type': 'human', 'review_status': 'approved',
            'source': 'youthcenter', 'external_id': '1',
        }]]
        with patch('benefitfinder.infrastructure.database.repositories.source_catalog_import.psycopg.connect') as connect:
            connect.return_value.__enter__.return_value = connection
            self.assertEqual(load_source_release_identities('postgresql://virtual', ('youth:1',), ('1',)), ())
            second = load_source_release_identities('postgresql://virtual', ('youth:1',), ('1',))
        self.assertEqual(second[0]['publication_status'], 'retired')
        self.assertEqual(connect.call_count, 2)
        self.assertIn('default_transaction_read_only=on', connect.call_args.kwargs['options'])
