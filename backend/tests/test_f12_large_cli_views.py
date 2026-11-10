"""Independent, virtual end-to-end comparison and provenance/error boundaries."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from backend.tests.test_f12_evaluation_files import (
    make_document, make_policy, valid_gold, write_json, write_pending,
)
from benefitfinder.ai.chunked_evidence import extract_chunked_evidence
from benefitfinder.infrastructure.chunk_evidence_files import write_chunk_checkpoint, write_chunked_run
from benefitfinder.infrastructure.f12_evaluation_files import EvaluationFileError, evaluate_manifest
from benefitfinder.infrastructure.policy_rule_files import load_rule_snapshot
from benefitfinder.jobs.__main__ import main


def chunk_fixture(root: Path) -> Path:
    document = make_document(text='Q1 가상 월 지원은 얼마인가요?\nA 가상 월 50만원을 지원합니다.\n* 가상 예외는 별도 검수합니다.\n')
    write_pending(root / 'ref', document, make_policy())
    write_pending(root / 'cand', document, make_policy())
    snapshot = load_rule_snapshot(root / 'cand/rules-pending.json')
    output = root / 'chunked'
    output.mkdir()
    def chat(*args: object) -> str:
        return json.dumps({'eligibility': [], 'amount': [2], 'timing': [], 'exceptions': [3]})
    facts, provenance = extract_chunked_evidence(
        snapshot.batch, chat=chat,
        checkpoint=lambda record: write_chunk_checkpoint(output, record),
    )
    write_chunked_run(output, snapshot, facts, provenance)
    gold = valid_gold(document)
    gold['items'][0]['candidate']['evidence'].update(quote=document.text.rstrip(), location='lines 1-3')
    write_json(root / 'gold.json', gold)
    manifest = root / 'manifest.json'
    write_json(manifest, {'format_version': 2, 'cases': [{
        'id': 'virtual-case', 'category': 'employment', 'reference_dir': 'ref',
        'approved_exports': ['gold.json'], 'candidate_dir': 'chunked', 'candidate_format': 'chunked-v1',
    }]})
    assert document.content_sha256 == provenance['source']['content_sha256']
    return manifest


class LargeCliViewsTests(TestCase):
    def test_both_views_are_separate_and_same_reference_is_used(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = chunk_fixture(root)
            report = evaluate_manifest(manifest)
            self.assertEqual(report['kind'], 'f12_extraction_comparison')
            self.assertEqual(report['assessment'], 'findings')
            self.assertEqual(report['views']['model']['summary']['partial_items'], 1)
            self.assertEqual(report['views']['context']['summary']['covered_items'], 1)
            for view in report['views'].values():
                self.assertEqual(view['summary']['reference_items'], 1)
                self.assertEqual(view['approval_effect'], 'none')
                self.assertEqual(view['auxiliary_exceptions']['items_evaluated'], 1)
                counts = view['input_accounting']
                self.assertEqual(counts['candidate_items_read'], counts['candidate_items_evaluated'])
            self.assertEqual(evaluate_manifest(manifest, view='model')['view'], 'model')
            self.assertEqual(evaluate_manifest(manifest, view='context')['view'], 'context')

    def test_cli_exit_codes_create_only_and_no_source_console(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = chunk_fixture(root)
            inputs = {p: p.read_bytes() for p in root.rglob('*.json')}
            for view, expected in [('both', 1), ('model', 1), ('context', 0)]:
                output = root / f'{view}.json'
                args = ['evaluate-f12', '--manifest', str(manifest), '--view', view, '--output', str(output)]
                stdout, stderr = StringIO(), StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    self.assertEqual(main(args), expected)
                self.assertNotIn('청년 취업 지원금은', stdout.getvalue() + stderr.getvalue())
                before = output.read_bytes()
                with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                    self.assertEqual(main(args), 2)
                self.assertEqual(output.read_bytes(), before)
            self.assertTrue(all(p.read_bytes() == data for p, data in inputs.items()))

    def test_trace_and_completion_mismatch_fail_without_report(self) -> None:
        for failure in ('completion', 'source', 'selection', 'context_added', 'missing_groups', 'incomplete_group'):
            with self.subTest(failure=failure), TemporaryDirectory() as temp:
                root = Path(temp)
                manifest = chunk_fixture(root)
                path = root / 'chunked/chunked-evidence.json'
                data = json.loads(path.read_text())
                if failure == 'completion':
                    (root / 'chunked/completion.json').unlink()
                elif failure == 'source':
                    data['source']['document_version'] = 'wrong-version'
                elif failure == 'selection':
                    data['chunks'][0]['selected']['amount'] = []
                elif failure == 'context_added':
                    data['chunks'][0]['context_added']['amount'] = []
                elif failure == 'missing_groups':
                    data['context_groups'] = []
                else:
                    data['chunks'][0]['contextualized']['amount'] = [[2, 3]]
                    data['chunks'][0]['context_added']['amount'] = [[3, 3]]
                    facts_path = root / 'chunked/facts-pending.json'
                    facts = json.loads(facts_path.read_text())
                    quote = facts['benefit_amount']['items'][0]['evidence']['quote']
                    facts['benefit_amount']['items'][0]['evidence'].update(
                        quote='\n'.join(quote.splitlines()[1:]), location='text lines 2-3')
                    write_json(facts_path, facts)
                write_json(path, data)
                output = root / 'failed.json'
                with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                    self.assertEqual(main(['evaluate-f12', '--manifest', str(manifest),
                                           '--output', str(output)]), 2)
                self.assertFalse(output.exists())

    def test_added_context_cannot_change_numeric_model_values(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = chunk_fixture(root)
            path = root / 'chunked/facts-pending.json'
            data = json.loads(path.read_text())
            data['benefit_amount']['items'][0]['amounts_won'] = [999999]
            write_json(path, data)
            with self.assertRaisesRegex(EvaluationFileError, '^invalid_f12_evaluation_input$'):
                evaluate_manifest(manifest)

    def test_failed_extraction_never_fabricates_model_or_context_results(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = chunk_fixture(root)
            data = json.loads(manifest.read_text())
            data['cases'][0].update(candidate_dir=None, candidate_error='context_limit')
            write_json(manifest, data)
            report = evaluate_manifest(manifest)
            for view in report['views'].values():
                self.assertEqual(view['summary']['not_comparable_items'], 1)
                self.assertIsNone(view['summary']['approved_evidence_recall'])
                self.assertEqual(view['input_accounting']['candidate_items_evaluated'], 0)
                self.assertEqual(view['input_formats'][0]['view_origin'], 'unavailable')

    def test_auxiliary_exception_grounding_is_not_silently_ignored(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = chunk_fixture(root)
            path = root / 'chunked/chunked-evidence.json'
            data = json.loads(path.read_text())
            data['exceptions'][0]['evidence']['quote'] = 'virtual fabricated private-marker'
            write_json(path, data)
            report = evaluate_manifest(manifest, view='context')
            self.assertEqual(report['assessment'], 'findings')
            self.assertEqual(report['summary']['citation_errors'], 0)
            self.assertEqual(report['auxiliary_exceptions']['citation_errors'], 1)

    def test_duplicate_keys_and_invalid_view_preserve_fixed_errors(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = chunk_fixture(root)
            for view in ('invalid', ''):
                with self.subTest(view=view), self.assertRaisesRegex(
                    EvaluationFileError, '^invalid_f12_evaluation_input$'
                ):
                    evaluate_manifest(manifest, view=view)
            manifest.write_text('{"format_version":2,"format_version":2,"private-marker":true}')
            output = root / 'failed.json'
            stdout, stderr = StringIO(), StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(main(['evaluate-f12', '--manifest', str(manifest), '--output', str(output)]), 2)
            self.assertIn('invalid_f12_evaluation_input', stderr.getvalue())
            self.assertNotIn('private-marker', stdout.getvalue() + stderr.getvalue())
            self.assertFalse(output.exists())
