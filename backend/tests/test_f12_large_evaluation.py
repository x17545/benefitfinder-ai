"""Large-reader boundaries use virtual fixtures and bounded budget overrides."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from backend.tests.test_f12_evaluation_files import setup_valid_tree, write_json
from benefitfinder.infrastructure.f12_evaluation_files import EvaluationFileError, evaluate_manifest
from benefitfinder.jobs.__main__ import main


def large_fixture(root: Path, count: int) -> Path:
    manifest, _, _ = setup_valid_tree(root)
    path = root / 'cand/facts-pending.json'
    facts = json.loads(path.read_text())
    amount = facts['benefit_amount']['items'][0]
    for name in ('eligibility', 'benefit_amount', 'payment_timing'):
        item = amount if name == 'benefit_amount' else {'evidence': amount['evidence']}
        facts[name].update(status='extracted', items=[item] * count)
    write_json(path, facts)
    data = json.loads(manifest.read_text())
    data['format_version'] = 2
    write_json(manifest, data)
    return manifest


class LargeEvaluationBoundsTests(TestCase):
    def test_all_three_sections_include_100_and_101_items_without_truncation(self) -> None:
        for count in (100, 101):
            with self.subTest(count=count), TemporaryDirectory() as temp:
                root = Path(temp)
                manifest = large_fixture(root, count)
                report = evaluate_manifest(manifest)
                self.assertEqual(report['assessment'], 'no_findings')
                for view in report['views'].values():
                    counts = view['input_accounting']
                    self.assertEqual(counts['candidate_items_read'], count * 3)
                    self.assertEqual(counts['candidate_items_evaluated'], count * 3)
                    self.assertEqual(view['summary']['candidate_citations'], count * 3)
                    self.assertEqual(view['input_formats'][0]['view_origin'], 'provided_shared')
                    self.assertTrue(view['requires_human_review'])
                    self.assertEqual(view['approval_effect'], 'none')

    def test_legacy_v1_limit_and_report_semantics_are_preserved(self) -> None:
        for count in (100, 101):
            with self.subTest(count=count), TemporaryDirectory() as temp:
                manifest = large_fixture(Path(temp), count)
                data = json.loads(manifest.read_text())
                data['format_version'] = 1
                write_json(manifest, data)
                if count == 101:
                    with self.assertRaisesRegex(EvaluationFileError, '^invalid_f12_evaluation_input$'):
                        evaluate_manifest(manifest)
                else:
                    report = evaluate_manifest(manifest)
                    self.assertEqual(report['format_version'], 1)
                    self.assertNotIn('views', report)
                    self.assertEqual(report['summary']['candidate_citations'], count * 3)
                with self.assertRaisesRegex(EvaluationFileError, '^invalid_f12_evaluation_input$'):
                    evaluate_manifest(manifest, view='model')

    def test_bad_101st_citation_is_reported_not_skipped(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = large_fixture(root, 101)
            path = root / 'cand/facts-pending.json'
            facts = json.loads(path.read_text())
            facts['benefit_amount']['items'][100]['evidence']['quote'] = 'virtual invented citation'
            write_json(path, facts)
            report = evaluate_manifest(manifest, view='context')
            self.assertEqual(report['assessment'], 'findings')
            self.assertEqual(report['summary']['candidate_citations'], 303)
            self.assertEqual(report['summary']['grounded_candidate_citations'], 302)
            self.assertEqual(report['summary']['citation_errors'], 1)
            item = next(i for i in report['cases'][0]['candidates'] if i['id'] == 'amount:101')
            self.assertEqual(item['errors'][0]['code'], 'quote_not_in_source')

    def test_budget_errors_are_distinct_and_never_write_partial_reports(self) -> None:
        caps = {'_MAX_FILE_BYTES': 8, '_MAX_TOTAL_BYTES': 100, '_MAX_FILES': 1,
                '_MAX_CANDIDATES': 1, '_MAX_CITATIONS': 1, '_MAX_REFERENCE_ITEMS': 0,
                '_MAX_WORK': 1, '_MAX_LINE_WORK': 1, '_MAX_SECTION_ITEMS': 100,
                '_MAX_SOURCE_CHARACTERS': 1, '_MAX_SOURCE_LINES': 1, '_MAX_QUOTE_CHARACTERS': 1}
        with TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = large_fixture(root, 101)
            before = {p: p.read_bytes() for p in root.rglob('*.json')}
            for name, value in caps.items():
                with self.subTest(cap=name), patch('benefitfinder.infrastructure.f12_evaluation_files.' + name, value):
                    output = root / (name + '.json')
                    stderr = StringIO()
                    with redirect_stdout(StringIO()), redirect_stderr(stderr):
                        self.assertEqual(main(['evaluate-f12', '--manifest', str(manifest),
                                               '--output', str(output)]), 2)
                    self.assertIn('f12_evaluation_budget_exceeded', stderr.getvalue())
                    self.assertFalse(output.exists())
            self.assertTrue(all(p.read_bytes() == raw for p, raw in before.items()))

    def test_invalid_type_after_first_hundred_is_not_a_budget_error(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = large_fixture(root, 101)
            path = root / 'cand/facts-pending.json'
            facts = json.loads(path.read_text())
            facts['benefit_amount']['items'][100]['amounts_won'] = [True]
            write_json(path, facts)
            with self.assertRaisesRegex(EvaluationFileError, '^invalid_f12_evaluation_input$'):
                evaluate_manifest(manifest)

    def test_invalid_unicode_returns_fixed_error_without_partial_output(self) -> None:
        for version in (1, 2):
            with self.subTest(version=version), TemporaryDirectory() as temp:
                root = Path(temp)
                manifest = large_fixture(root, 100)
                data = json.loads(manifest.read_text())
                data['format_version'] = version
                data['cases'][0]['id'] = 'private-marker-\ud800'
                manifest.write_text(json.dumps(data, ensure_ascii=True), encoding='utf-8')
                before = {p: p.read_bytes() for p in root.rglob('*.json')}
                output = root / 'failed.json'
                stdout, stderr = StringIO(), StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    self.assertEqual(main(['evaluate-f12', '--manifest', str(manifest),
                                           '--output', str(output)]), 2)
                self.assertIn('invalid_f12_evaluation_input', stderr.getvalue())
                self.assertNotIn('private-marker', stdout.getvalue() + stderr.getvalue())
                self.assertFalse(output.exists())
                self.assertTrue(all(p.read_bytes() == raw for p, raw in before.items()))
