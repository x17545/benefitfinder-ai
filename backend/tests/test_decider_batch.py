"""Fake inference verifies orchestration only; real GPU results are separate artifacts."""

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from benefitfinder.ai.decider_extraction import analyze_batch, decision_request, text_windows
from benefitfinder.application.decider import (
    Compilation, DeciderError, KINDS, LocatedSpan, MODEL_ID, MODEL_REVISION, QUESTION_VERSION, canonical_hash,
)
from benefitfinder.application.decider_evaluation import evaluate_report, require_evaluation
from benefitfinder.application.policy_analysis import AnalysisPolicy, AnalysisSource, PolicyAnalysisInput
from benefitfinder.application.policy_rules import SourceDocument
from benefitfinder.infrastructure.decider_client import CheckpointDecider, HostedDecider, validate_responses, private_json
from benefitfinder.jobs.decider_batch import run_batch


def policy(number: str) -> PolicyAnalysisInput:
    policy_id = 'youth:' + number
    document = SourceDocument('doc-' + number, policy_id, 'https://example.org/' + number,
        'fictional-v1', datetime(2026, 11, 1, tzinfo=timezone.utc),
        '가상 공고 ' + number + '\n매월 10만원을 3개월 지급한다.\n다른 정책의 중복 수급은 확인 필요.', True)
    return PolicyAnalysisInput(policy_id, (document,),
        (AnalysisPolicy(policy_id, '가상 공고 ' + number, ('employment',)),),
        (AnalysisSource('youthcenter', number, 'a' * 64),))


def evaluation_fixture(
    *,
    count: int = 10,
    provider: str = 'local',
    failed_policies: frozenset[str] = frozenset(),
    amount_cases: int | None = None,
    wrong_amount: frozenset[int] = frozenset(),
) -> tuple[dict, dict, dict[str, dict]]:
    features = ('fixed', 'cap', 'exceptions', 'missing', 'contradiction', 'overlap')
    report_entries = []
    gold_cases = []
    extractions = {}
    for index in range(count):
        policy_id = f'youth:eval-{index}'
        input_sha256 = canonical_hash({'evaluation_policy': policy_id})
        expected_decisions = {'overlap_kind': 'restricted', 'scope': 'youth_employment_or_housing',
                              'coverage': 'complete', 'eligibility_kind': 'simple'}
        if amount_cases is None or index < amount_cases:
            expected_decisions['amount_kind'] = 'fixed'
        actual_decisions = dict(expected_decisions)
        if index in wrong_amount and 'amount_kind' in actual_decisions:
            actual_decisions['amount_kind'] = 'cap'
        document_id = 'doc-evaluation'
        span = {'kind': 'amount', 'start': 0, 'end': 4}
        report_entries.append({
            'policy_id': policy_id,
            'input_sha256': input_sha256,
            'status': 'failed' if policy_id in failed_policies else 'completed',
        })
        gold_cases.append({
            'policy_id': policy_id,
            'input_sha256': input_sha256,
            'features': list(features),
            'reviewed_fields': list(KINDS),
            'documents': [{
                'document_id': document_id,
                'spans': [span],
                'decisions': expected_decisions,
            }],
        })
        extractions[policy_id] = {
            'documents': [{
                'document_id': document_id,
                'spans': [span],
                'decisions': actual_decisions,
            }],
        }
    model = {
        'id': MODEL_ID,
        'revision': MODEL_REVISION,
        'questions': QUESTION_VERSION,
        'provider': provider,
    }
    return (
        {'entries': report_entries, 'model': model},
        {'format_version': 1, 'cases': gold_cases},
        extractions,
    )


class FakeDecider:
    def __init__(self):
        self.requests = []

    @property
    def identity(self):
        return {'id': MODEL_ID, 'revision': MODEL_REVISION, 'questions': QUESTION_VERSION, 'provider': 'mock'}

    def decide(self, requests):
        self.requests.append(requests)
        results = []
        for request in requests:
            if next(iter(request['questions'].values()))['type'] == 'locate':
                start = request['state'].find('10만원')
                answers = {key: {'spans': [{'start': start, 'end': start + 4, 'text': '10만원', 'score': 0.9}]
                                    if start >= 0 and key == 'amount' else []}
                           for key in request['questions']}
            else:
                # The next call contains earlier extracted spans, from this policy only.
                state = json.loads(request['state'])
                assert all('10만원' in span['text'] for doc in state['documents'] for span in doc['located'])
                answers = {key: {'choice': next(iter(q['criteria']))} if q['type'] == 'choice'
                           else {'noul': 1.0} if q['type'] == 'noul' else {'score': 2.0}
                           for key, q in request['questions'].items()}
            results.append({'answers': answers, 'usage': {'input_tokens': 20, 'output_tokens': 0, 'sequences': 1}})
        return validate_responses(requests, results)


class DeciderBatchTest(unittest.TestCase):
    def test_followup_keeps_full_source_and_every_unique_evidence_kind(self):
        value = policy('1')
        doc = value.documents[0]
        start = doc.text.index('10만원')
        amount = LocatedSpan('amount', start, start + 4, '10만원', 0.9)
        overlap = replace(amount, kind='overlap')
        request = decision_request(value, {doc.id: [amount, amount, overlap]})
        state = json.loads(request['state'])['documents'][0]
        self.assertEqual(state['text'], doc.text)
        self.assertCountEqual(state['located'], [{'kind': 'amount', 'text': '10만원'},
                                                {'kind': 'overlap', 'text': '10만원'}])

    def test_batch_separates_policies_and_dependencies(self):
        client = FakeDecider()
        values = (policy('1'), policy('2'))
        extracted, errors = analyze_batch(values, client, batch_size=4)
        self.assertFalse(errors)
        self.assertEqual([len(batch) for batch in client.requests], [2, 2])
        for request, value in zip(client.requests[0], values, strict=True):
            self.assertEqual(request['state'], value.documents[0].text)
        for value in values:
            span = extracted[value.policy_id][0].spans[0]
            self.assertEqual(value.documents[0].text[span.start:span.end], span.text)

    def test_windows_cover_all_characters_without_silent_truncation(self):
        text = '한글🙂\n' * 1500
        windows = text_windows(text, 500)
        covered = {i for start, end in windows for i in range(start, end)}
        self.assertEqual(covered, set(range(len(text))))
        self.assertTrue(all(end - start <= 500 for start, end in windows))

    def test_one_length_failure_does_not_drop_another_policy(self):
        class Limited(FakeDecider):
            def decide(self, requests):
                if any('가상 공고 1' in request['state'] for request in requests):
                    raise DeciderError('decider_input_length_exceeded')
                return super().decide(requests)
        extracted, errors = analyze_batch((policy('1'), policy('2')), Limited())
        self.assertEqual(errors, {'youth:1': 'decider_input_length_exceeded'})
        self.assertEqual(set(extracted), {'youth:2'})

    def test_cached_restart_and_changed_source(self):
        with TemporaryDirectory() as directory:
            raw = FakeDecider()
            client = CheckpointDecider(raw, Path(directory))
            value = policy('1')
            original, _ = analyze_batch((value,), client)
            count = len(raw.requests)
            restarted, _ = analyze_batch((value,), client)
            self.assertEqual(original, restarted)
            self.assertEqual(len(raw.requests), count)
            changed = replace(value, documents=(replace(value.documents[0], text=value.documents[0].text + '\n수정'),))
            analyze_batch((changed,), client)
            self.assertGreater(len(raw.requests), count)

    def test_tampered_spans_and_nonfinite_scores_are_rejected(self):
        request = {'state': '가상 10만원', 'questions': {'amount': {'type': 'locate'}}}
        for span in ({'start': 3, 'end': 7, 'text': '20만원', 'score': 1.0},
                     {'start': 3, 'end': 7, 'text': '10만원', 'score': float('nan')}):
            with self.subTest(span=span), self.assertRaises(DeciderError):
                validate_responses([request], [{'answers': {'amount': {'spans': [span]}}}])

    def test_held_policy_is_never_counted_or_sent_as_published(self):
        with TemporaryDirectory() as directory:
            sent = []
            report = run_batch((policy('1'),), FakeDecider(), root=Path(directory),
                compile_policy=lambda *_: Compilation(None, ('amount_is_cap',), {}),
                analyze=analyze_batch, save=private_json, publish=lambda *args: sent.append(args))
            self.assertEqual(report['counts']['held'], 1)
            self.assertEqual(report['counts']['published'], 0)
            self.assertFalse(sent)

    def test_existing_policy_alias_is_held_without_publishing_a_duplicate(self):
        def reject_alias(*args):
            raise ValueError('ai_catalog_policy_alias_conflict')
        with TemporaryDirectory() as directory:
            report = run_batch((policy('1'),), FakeDecider(), root=Path(directory),
                compile_policy=lambda *_: Compilation({'fictional': True}, (), {}),
                analyze=analyze_batch, save=private_json, publish=reject_alias)
            self.assertEqual(report['counts']['published'], 0)
            self.assertEqual(report['counts']['held'], 1)
            self.assertEqual(report['entries'][0]['reasons'], ['ai_catalog_policy_alias_conflict'])

    def test_unpassed_or_different_model_evaluation_cannot_publish(self):
        with self.assertRaises(ValueError):
            require_evaluation({'eligible_for_publication': False}, FakeDecider().identity)
        with self.assertRaises(ValueError):
            require_evaluation({'eligible_for_publication': True, 'model': {}}, FakeDecider().identity)

    def test_failed_entry_cannot_reuse_stale_extraction(self):
        report, gold, extractions = evaluation_fixture(count=10)
        failed_policy = report['entries'][0]['policy_id']
        report['entries'][0]['status'] = 'failed'

        class RejectFailedLookup:
            def get(self, key: str, default=None):
                if key == failed_policy:
                    raise AssertionError('failed policy extraction was read')
                return extractions.get(key, default)

        result = evaluate_report(report, gold, RejectFailedLookup())
        self.assertEqual(result['totals']['failed_policies'], 1)
        self.assertEqual(result['totals']['predicted_characters'], 36)
        self.assertEqual(result['totals']['matched_characters'], 36)
        self.assertEqual(result['totals']['decision_checks'], 50)
        self.assertFalse(result['eligible_for_publication'])

    def test_per_decision_key_accuracy_blocks_aggregate_pass(self):
        report, gold, extractions = evaluation_fixture(
            count=20, amount_cases=5, wrong_amount=frozenset({0}),
        )
        result = evaluate_report(report, gold, extractions)
        self.assertGreater(result['decision_accuracy'], 0.95)
        self.assertLess(result['decisions']['amount_kind']['accuracy'], 0.95)
        self.assertEqual(result['decisions']['overlap_kind']['accuracy'], 1.0)
        self.assertFalse(result['eligible_for_publication'])
        with self.assertRaisesRegex(ValueError, 'decider_evaluation_not_passed'):
            require_evaluation(result, report['model'])

    def test_mock_provider_cannot_qualify_even_with_perfect_metrics(self):
        report, gold, extractions = evaluation_fixture(provider='mock')
        result = evaluate_report(report, gold, extractions)
        self.assertEqual(result['totals']['correct_decisions'], result['totals']['decision_checks'])
        self.assertEqual(result['precision'], 1.0)
        self.assertEqual(result['recall'], 1.0)
        self.assertFalse(result['eligible_for_publication'])
        with self.assertRaisesRegex(ValueError, 'decider_evaluation_not_passed'):
            require_evaluation(result, report['model'])

    def test_complete_reference_is_required_for_each_field_and_decision(self):
        report, gold, extractions = evaluation_fixture()
        complete = evaluate_report(report, gold, extractions)
        self.assertTrue(complete['eligible_for_publication'])
        require_evaluation(complete, report['model'])
        gold['cases'][0]['reviewed_fields'].remove('exceptions')
        incomplete = evaluate_report(report, gold, extractions)
        self.assertFalse(incomplete['eligible_for_publication'])
        gold['cases'][0]['reviewed_fields'] = list(KINDS)
        del gold['cases'][0]['documents'][0]['decisions']['eligibility_kind']
        self.assertFalse(evaluate_report(report, gold, extractions)['eligible_for_publication'])

    def test_evaluation_rejects_gold_source_hash_mismatch(self):
        report, gold, extractions = evaluation_fixture()
        gold['cases'][0]['input_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'decider_gold_source_mismatch'):
            evaluate_report(report, gold, extractions)

    def test_partial_gold_coverage_cannot_authorize_even_with_perfect_local_output(self):
        report, gold, extractions = evaluation_fixture()
        gold['coverage'] = 'critical_evidence_only'
        result = evaluate_report(report, gold, extractions)
        self.assertEqual(result['gold_coverage'], 'critical_evidence_only')
        self.assertEqual(result['model']['provider'], 'local')
        self.assertEqual(result['precision'], 1.0)
        self.assertEqual(result['recall'], 1.0)
        self.assertEqual(result['decision_accuracy'], 1.0)
        self.assertFalse(result['eligible_for_publication'])
        with self.assertRaisesRegex(ValueError, 'decider_evaluation_not_passed'):
            require_evaluation({**result, 'eligible_for_publication': True}, report['model'])

    def test_hosted_capabilities_probe_requires_bounded_max_requests(self):
        capabilities = {
            'model': MODEL_ID,
            'revision': MODEL_REVISION,
            'offsets': 'unicode_codepoints',
            'fast': False,
            'tasks': ['locate', 'choice', 'noul', 'score'],
            'limits': {'max_requests': 2},
        }
        with TemporaryDirectory() as directory:
            budget = Path(directory) / 'budget.json'
            with patch.object(HostedDecider, '_post', return_value=capabilities) as probe:
                client = HostedDecider('https://decider.invalid/v1', 'virtual-token',
                                       budget_path=budget)
            probe.assert_called_once_with({'operation': 'capabilities'})
            self.assertEqual(client.max_requests, 2)

            for limits in ({}, {'max_requests': 0}, {'max_requests': 33},
                           {'max_requests': True}, {'max_requests': 1.5}):
                with self.subTest(limits=limits), patch.object(
                    HostedDecider, '_post', return_value={**capabilities, 'limits': limits},
                ):
                    with self.assertRaisesRegex(DeciderError, 'decider_endpoint_limits_required'):
                        HostedDecider('https://decider.invalid/v1', 'virtual-token',
                                      budget_path=budget)

    def test_hosted_decide_splits_requests_at_capability_limit(self):
        capabilities = {
            'model': MODEL_ID,
            'revision': MODEL_REVISION,
            'offsets': 'unicode_codepoints',
            'fast': False,
            'tasks': ['locate', 'choice', 'noul', 'score'],
            'limits': {'max_requests': 2},
        }
        requests = [
            {'state': f'가상 요청 {index}',
             'questions': {'choice': {'type': 'choice', 'criteria': {'yes': '예'}}}}
            for index in range(3)
        ]
        response = {
            'answers': {'choice': {'choice': 'yes'}},
            'usage': {'input_tokens': 1, 'output_tokens': 1, 'sequences': 1},
        }
        with TemporaryDirectory() as directory:
            with patch.object(HostedDecider, '_post', return_value=capabilities):
                client = HostedDecider('https://decider.invalid/v1', 'virtual-token',
                                       budget_path=Path(directory) / 'budget.json')

            def post(value: dict) -> dict:
                return {'responses': [response] * len(value['requests'])}

            with patch.object(client, '_post', side_effect=post) as calls:
                result = client.decide(requests)
        self.assertEqual(result, [response] * len(requests))
        self.assertEqual(
            [len(call.args[0]['requests']) for call in calls.call_args_list],
            [2, 1],
        )

    def test_persisted_daily_api_budget_counts_ambiguous_attempts(self):
        with TemporaryDirectory() as directory:
            client = object.__new__(HostedDecider)
            client.budget_path, client.daily_calls = Path(directory) / 'budget.json', 2
            client._reserve()
            client._reserve()
            restarted = object.__new__(HostedDecider)
            restarted.budget_path, restarted.daily_calls = client.budget_path, 2
            with self.assertRaisesRegex(DeciderError, 'daily_budget'):
                restarted._reserve()
            self.assertEqual(json.loads(client.budget_path.read_text())['calls'], 2)


if __name__ == '__main__':
    unittest.main()
