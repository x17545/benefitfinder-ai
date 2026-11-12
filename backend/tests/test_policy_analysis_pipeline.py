"""Offline input, failure, immutable artifacts and daily ordering contracts."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from benefitfinder.application.policy_analysis import (
    AnalysisClaim, AnalysisPolicy, AnalysisSource, AnalysisStep, PolicyAnalysisInput,
    PolicyAnalysisResult,
)
from benefitfinder.application.policy_facts import PolicyFactsDraft
from benefitfinder.application.policy_rules import SourceDocument
from benefitfinder.application.run_policy_analysis import run_pending_analysis
from benefitfinder.infrastructure.policy_analysis_files import PolicyAnalysisFiles
from benefitfinder.infrastructure.policy_analysis_inputs import (
    analysis_input_from_payload, load_policy_analysis_input,
)
from benefitfinder.jobs.daily_refresh import run_daily_refresh

AT = datetime(2026, 11, 2, 1, tzinfo=timezone.utc)


def analysis_input(*, policy_id='youth:fictional:A', material='a' * 64, at=AT):
    return PolicyAnalysisInput(policy_id, (
        SourceDocument('fictional-document', policy_id, 'https://example.test/fictional',
                       'fictional-v1', at, '가상 정책: 만 19세 이상 신청할 수 있습니다.', True),
    ), (AnalysisPolicy(policy_id, '가상 청년 취업 지원', ('employment',)),), (
        AnalysisSource('youthcenter', policy_id.rsplit(':', 1)[-1], material),
    ))


def claim():
    return AnalysisClaim(1, 'a' * 32, 1, analysis_input(), AT + timedelta(minutes=10))


class PolicyAnalysisPipelineTest(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_input_roundtrip_and_fingerprint_preserve_exact_text(self):
        value = analysis_input()
        path = self.root / 'input.json'
        path.write_text(json.dumps({'format_version': 1, **value.payload()}))
        loaded = load_policy_analysis_input(path)
        self.assertEqual(loaded, value)
        self.assertEqual(loaded.fingerprint(), value.fingerprint())
        changed = replace(value, documents=(replace(value.documents[0], text='다른 가상 문장'),))
        self.assertNotEqual(changed.fingerprint(), value.fingerprint())

    def test_input_rejects_profiles_approval_duplicate_keys_and_nan(self):
        path = self.root / 'input.json'
        for encoded in ('{"format_version":1,"format_version":1}', '{"a": NaN}',
                        json.dumps({'format_version': 1, **analysis_input().payload(),
                                    'profile': {'income': 12}}),
                        json.dumps({'format_version': 1, **analysis_input().payload(),
                                    'approved': True})):
            path.write_text(encoded)
            with self.assertRaisesRegex(ValueError, '^invalid_policy_analysis_input$'):
                load_policy_analysis_input(path)

    def test_non_official_unknown_category_wrong_policy_and_oversize_rejected(self):
        value = analysis_input()
        for change in ({'official': False}, {'policy_id': 'youth:other'}, {'text': 'x' * 100_001}):
            with self.assertRaises(ValueError):
                replace(value, documents=(replace(value.documents[0], **change),))
        with self.assertRaises(ValueError):
            replace(value, policies=(replace(value.policies[0], categories=('education',)),))
        with self.assertRaises(ValueError):
            replace(value, collection_sources=(replace(value.collection_sources[0], material_sha256='bad'),))
        payload = value.payload()
        payload['documents'][0]['collected_at'] = '2026-11-01'
        with self.assertRaises(ValueError):
            analysis_input_from_payload(payload)

    def test_private_artifacts_and_trace_contain_no_tool_arguments_or_approval(self):
        item = claim()
        files = PolicyAnalysisFiles(self.root, lambda *_: None)
        files.start(item)
        step = AnalysisStep('fetch_document', 'ok', 1)
        files.step(item, step)
        result = PolicyAnalysisResult((), (PolicyFactsDraft(item.input.documents[0], (), (), ()),), (), (), (step,))
        reference, digest, summary = files.finish(item, result)
        self.assertEqual(summary['documents'], 1)
        self.assertEqual(len(digest), 64)
        directory = self.root / reference
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        for path in directory.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        trace = (directory / 'trace.jsonl').read_text()
        self.assertEqual(json.loads(trace), {'tool': 'fetch_document', 'outcome': 'ok', 'elapsed_ms': 1})
        self.assertNotIn('가상 정책', trace)
        self.assertNotIn('approved', (directory / 'manifest.json').read_text())
        with self.assertRaisesRegex(ValueError, 'f12_artifact_failed'):
            files.start(item)

    def test_trace_rejects_raw_failure_message_and_finish_requires_matching_trace(self):
        item = claim()
        files = PolicyAnalysisFiles(self.root, lambda *_: None)
        files.start(item)
        with self.assertRaisesRegex(ValueError, 'f12_artifact_failed'):
            files.step(item, AnalysisStep('fetch_document', 'raw source / KEY', 1))
        with self.assertRaisesRegex(ValueError, 'f12_artifact_failed'):
            files.finish(item, PolicyAnalysisResult((), (), (), (), (AnalysisStep('fetch_document', 'ok', 1),)))

    def test_artifact_directory_cannot_escape_through_symlink(self):
        with TemporaryDirectory() as outside:
            (self.root / 'f12-analysis').symlink_to(outside)
            files = PolicyAnalysisFiles(self.root, lambda *_: None)
            with self.assertRaisesRegex(ValueError, 'f12_artifact_failed'):
                files.start(claim())
            self.assertEqual(list(Path(outside).iterdir()), [])

    def test_failed_analysis_retries_without_claiming_review_readiness_and_keeps_trace(self):
        item = claim()
        tasks = Mock()
        tasks.claim.side_effect = [item, None]
        tasks.is_current.return_value = True
        tasks.fail.return_value = 'pending'
        files = PolicyAnalysisFiles(self.root, lambda *_: None)

        def analyze(_claim, on_step):
            on_step(AnalysisStep('fetch_document', 'ok', 1))
            raise RuntimeError('PRIVATE raw source secret')

        counts = run_pending_analysis(tasks, files, analyze)
        self.assertEqual(counts, {'attempted': 1, 'needs_review': 0, 'stale': 0, 'failed': 1})
        tasks.complete.assert_not_called()
        tasks.fail.assert_called_once_with(item, 'f12_analysis_failed')
        directory = self.root / f'f12-analysis/1/{item.token}'
        self.assertEqual(len((directory / 'trace.jsonl').read_text().splitlines()), 1)
        self.assertNotIn('PRIVATE', (directory / 'failure.json').read_text())

    def test_source_change_discards_analysis_before_artifact_completion(self):
        tasks, files = Mock(), Mock()
        tasks.claim.side_effect = [claim(), None]
        tasks.is_current.side_effect = [True, False]
        tasks.fail.return_value = 'stale'
        counts = run_pending_analysis(tasks, files, Mock())
        self.assertEqual(counts['stale'], 1)
        files.finish.assert_not_called()
        tasks.complete.assert_not_called()

    def test_invalid_limit_does_not_claim(self):
        tasks = Mock()
        for limit in (True, 0, 21):
            with self.assertRaises(ValueError):
                run_pending_analysis(tasks, Mock(), Mock(), limit=limit)
        tasks.claim.assert_not_called()

    def test_daily_analysis_runs_after_both_sources_and_before_one_notification(self):
        calls = []
        def analyze():
            self.assertCountEqual(calls, ['policies', 'rate'])
            calls.append('analysis')
            return 1
        messages = []
        code = run_daily_refresh(
            self.root, refresh_policies=lambda: calls.append('policies') or 1,
            refresh_rate=lambda: calls.append('rate') or 0, analyze_pending=analyze,
            clock=lambda: AT, notify=messages.append,
        )
        self.assertEqual(code, 1)
        self.assertEqual(len(messages), 1)
        self.assertIn('F12 준비된 문서 분석: 실패', messages[0])
        self.assertEqual(json.loads((self.root / 'status.json').read_text())['last_attempt']['stages']['f12']['exit_code'], 1)

    def test_daily_analysis_reports_counts_and_preparation_waiting_separately(self):
        messages = []
        counts = {'attempted': 1, 'needs_review': 1, 'stale': 0, 'failed': 0,
                  'source_records_awaiting_scope_or_documents': 12}
        self.assertEqual(run_daily_refresh(
            self.root, refresh_policies=lambda: 0, refresh_rate=lambda: 0,
            analyze_pending=lambda: counts, clock=lambda: AT, notify=messages.append,
        ), 0)
        self.assertIn('검수 대기 생성: 1건', messages[0])
        self.assertIn('분류·문서 준비 대기 원천: 12건', messages[0])

    def test_daily_rejects_unrecognized_analysis_result_without_leaking_it(self):
        messages = []
        self.assertEqual(run_daily_refresh(
            self.root, refresh_policies=lambda: 0, refresh_rate=lambda: 0,
            analyze_pending=lambda: {'profile': 'PRIVATE'}, clock=lambda: AT, notify=messages.append,
        ), 1)
        self.assertNotIn('PRIVATE', messages[0])
        self.assertNotIn('PRIVATE', (self.root / 'status.json').read_text())
