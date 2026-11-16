"""Fake inference verifies structured-first preparation and bounded orchestration."""

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from benefitfinder.ai.decider_extraction import analyze_batch, text_windows
from benefitfinder.application.decider import (
    Compilation, DeciderError, DocumentExtraction, KINDS, LocatedSpan, MODEL_ID, MODEL_REVISION,
    QUESTION_VERSION, canonical_hash,
)
from benefitfinder.application.decider_evaluation import (
    EVALUATION_CRITERIA_VERSION,
    EVALUATION_THRESHOLDS,
    evaluate_report,
    require_evaluation,
)
from benefitfinder.application.policy_analysis import AnalysisPolicy, AnalysisSource, PolicyAnalysisInput
from benefitfinder.application.policy_rules import SourceDocument
from benefitfinder.infrastructure.decider_client import CheckpointDecider, HostedDecider, validate_responses, private_json
from benefitfinder.ingestion.decider_source_fields import prepare_source_fields
from benefitfinder.jobs.decider_batch import run_batch


def policy(number: str, *, text: str | None = None) -> PolicyAnalysisInput:
    policy_id = 'youth:' + number
    document = SourceDocument('doc-' + number, policy_id, 'https://example.org/' + number,
        'fictional-v1', datetime(2026, 11, 1, tzinfo=timezone.utc),
        text if text is not None else (
            '가상 공고 ' + number + '\n추가 자격: 미취업자\n'
            '매월 10만원을 3개월 지급한다.\n다른 정책의 중복 수급은 확인 필요.'
        ), True)
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
                'spans': [dict(span)],
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
    def __init__(self, eligibility_start: str = '미취업자', eligibility_end: str | None = None):
        self.requests = []
        self.eligibility_start = eligibility_start
        self.eligibility_end = eligibility_end

    @property
    def identity(self):
        return {'id': MODEL_ID, 'revision': MODEL_REVISION, 'questions': QUESTION_VERSION, 'provider': 'mock'}

    def decide(self, requests):
        self.requests.append(requests)
        results = []
        for request in requests:
            assert set(request['questions']) == {'eligibility', 'exceptions', 'overlap'}
            state = request['state']
            start = state.find(self.eligibility_start)
            end = start + len(self.eligibility_start) if start >= 0 else -1
            if self.eligibility_end is not None and start >= 0:
                end_start = state.find(self.eligibility_end, end)
                end = end_start + len(self.eligibility_end) if end_start >= 0 else -1
            spans = ([{'start': start, 'end': end, 'text': state[start:end], 'score': 0.9}]
                     if 0 <= start < end else [])
            answers = {
                kind: {'spans': spans if kind == 'eligibility' else []}
                for kind in request['questions']
            }
            results.append({'answers': answers, 'usage': {'input_tokens': 20, 'output_tokens': 0, 'sequences': 1}})
        return validate_responses(requests, results)


class DeciderBatchTest(unittest.TestCase):
    def test_batch_runs_one_three_kind_narrative_pass_per_policy(self):
        client = FakeDecider()
        values = (policy('1'), policy('2'))
        extracted, errors = analyze_batch(values, client, batch_size=4)
        self.assertFalse(errors)
        self.assertEqual([len(batch) for batch in client.requests], [2])
        for request, value in zip(client.requests[0], values, strict=True):
            self.assertIn(value.documents[0].text, request['state'])
            self.assertEqual(
                set(request['questions']), {'eligibility', 'exceptions', 'overlap'},
            )
        for value in values:
            evidence = extracted[value.policy_id][0]
            raw = next(
                span for span in evidence.raw_spans
                if span.kind == 'eligibility' and span.method == 'locate'
            )
            final = next(
                span for span in evidence.spans
                if span.kind == 'eligibility' and span.method == 'source_context'
            )
            self.assertEqual(raw.text, '미취업자')
            self.assertEqual(evidence.document.text[raw.start:raw.end], raw.text)
            self.assertEqual(evidence.document.text[final.start:final.end], final.text)
            self.assertIn(raw.text, final.text)
            self.assertIn('미취업자', final.text)
            self.assertEqual(evidence.preparation['question_count'], 3)
            self.assertEqual(evidence.preparation['followup_requests'], 0)

    def test_scalar_only_source_uses_structured_evidence_without_model(self):
        value = policy('1', text=(
            '[plcyNm]\n가상 청년 지원\n'
            '[plcyNo]\n1\n'
            '[jobCd]\n0013010\n'
            '[sprtTrgtAgeLmtYn]\nN\n'
            '[sprtTrgtMinAge]\n19\n'
            '[sprtTrgtMaxAge]\n34\n'
        ))
        client = FakeDecider()

        extracted, errors = analyze_batch((value,), client, prepare_source=prepare_source_fields)

        self.assertFalse(errors)
        self.assertFalse(client.requests)
        evidence = extracted[value.policy_id][0]
        self.assertEqual(evidence.decisions, {'analysis_mode': 'structured_first'})
        self.assertEqual({span.method for span in evidence.spans}, {'source_field'})
        self.assertEqual(
            {span.text for span in evidence.spans},
            {'0013010', 'N', '19', '34'},
        )
        preparation = evidence.preparation
        self.assertIsNotNone(preparation)
        self.assertEqual(preparation['body_fields'], [])
        self.assertEqual(preparation['model_requests'], 0)
        self.assertEqual(preparation['structured']['jobCd']['raw'], '0013010')

    def test_structured_fields_are_excluded_and_only_body_is_sent(self):
        value = policy('1', text=(
            '[plcyNm]\n가상 청년 지원\n'
            '[plcyNo]\n1\n'
            '[jobCd]\n0013010\n'
            '[addAplyQlfcCndCn]\n미취업자\n연 소득 3천만 원 이하\n'
        ))
        client = FakeDecider()

        extracted, errors = analyze_batch((value,), client, prepare_source=prepare_source_fields)

        self.assertFalse(errors)
        self.assertEqual(len(client.requests), 1)
        request = client.requests[0][0]
        self.assertNotIn('0013010', request['state'])
        self.assertNotIn('[jobCd]', request['state'])
        self.assertIn('[addAplyQlfcCndCn]\n미취업자', request['state'])
        evidence = extracted[value.policy_id][0]
        source_field = next(span for span in evidence.spans if span.method == 'source_field')
        context = next(span for span in evidence.spans if span.method == 'source_context')
        raw = next(span for span in evidence.raw_spans if span.method == 'locate')
        self.assertEqual(source_field.text, '0013010')
        self.assertEqual(raw.text, '미취업자')
        self.assertEqual(context.text, '미취업자\n연 소득 3천만 원 이하')
        self.assertEqual(evidence.document.text[context.start:context.end], context.text)
        self.assertEqual(evidence.document.text[raw.start:raw.end], raw.text)
        self.assertIn(raw.text, context.text)
        self.assertEqual(
            [field['name'] for field in evidence.preparation['body_fields']],
            ['addAplyQlfcCndCn'],
        )

    def test_unknown_free_field_is_preserved_in_the_narrative_input(self):
        value = policy('1', text=(
            '[plcyNm]\n가상 청년 지원\n'
            '[plcyNo]\n1\n'
            '[newAgencyNote]\n새 제도 시행 전 담당자 확인 필요\n'
        ))
        client = FakeDecider()

        extracted, errors = analyze_batch((value,), client, prepare_source=prepare_source_fields)

        self.assertFalse(errors)
        self.assertEqual(len(client.requests), 1)
        state = client.requests[0][0]['state']
        self.assertIn('[newAgencyNote]\n새 제도 시행 전 담당자 확인 필요', state)
        body_fields = extracted[value.policy_id][0].preparation['body_fields']
        self.assertEqual([field['name'] for field in body_fields], ['newAgencyNote'])
        self.assertEqual(body_fields[0]['text'], '새 제도 시행 전 담당자 확인 필요')

    def test_windows_cover_all_characters_without_silent_truncation(self):
        text = '한글🙂\n' * 1500
        windows = text_windows(text, 500)
        covered = {i for start, end in windows for i in range(start, end)}
        self.assertEqual(covered, set(range(len(text))))
        self.assertTrue(all(end - start <= 500 for start, end in windows))

    def test_unknown_code_and_duplicate_field_fail_before_first_request(self):
        cases = (
            ('[jobCd]\n9999999\n', 'source_code_unknown:jobCd'),
            (
                '[addAplyQlfcCndCn]\n미취업자\n'
                '[addAplyQlfcCndCn]\n재학생\n',
                'source_field_duplicated',
            ),
        )
        for text, issue in cases:
            with self.subTest(issue=issue):
                value = policy('1', text=text)
                client = FakeDecider()

                extracted, errors = analyze_batch(
                    (value,), client, prepare_source=prepare_source_fields,
                )

                self.assertFalse(extracted)
                self.assertEqual(
                    errors,
                    {value.policy_id: 'decider_source_preparation_requires_review'},
                )
                self.assertFalse(client.requests)

    def test_unicode_multifield_and_cross_field_span_maps_to_source(self):
        value = policy('1', text=(
            '[plcyNm]\n가상 청년 지원\n'
            '[plcyNo]\n1\n'
            '[addAplyQlfcCndCn]\n🙂청년자격\n'
            '[ptcpPrpTrgtCn]\n학생자격\n'
        ))
        client = FakeDecider('청년자격', '학생자격')

        extracted, errors = analyze_batch((value,), client, prepare_source=prepare_source_fields)

        self.assertFalse(errors)
        self.assertEqual(len(client.requests), 1)
        self.assertIn('🙂청년자격', client.requests[0][0]['state'])
        evidence = extracted[value.policy_id][0]
        raw = {span.text: span for span in evidence.raw_spans if span.method == 'locate'}
        final = {
            span.text: span for span in evidence.spans if span.method == 'source_context'
        }
        self.assertEqual(set(raw), {'청년자격', '학생자격'})
        self.assertEqual(set(final), {'🙂청년자격', '학생자격'})
        for span in (*raw.values(), *final.values()):
            self.assertEqual(evidence.document.text[span.start:span.end], span.text)
        self.assertEqual(raw['청년자격'].start, evidence.document.text.index('청년자격'))
        for span in raw.values():
            self.assertTrue(any(
                context.start <= span.start < span.end <= context.end
                for context in final.values()
            ))
        self.assertEqual(
            [field['name'] for field in evidence.preparation['body_fields']],
            ['addAplyQlfcCndCn', 'ptcpPrpTrgtCn'],
        )

    def test_policy_budget_is_preflighted_before_any_model_request(self):
        value = policy('1', text='🙂미취업자\n' * 80)
        scenarios = (
            {'max_policy_characters': 100},
            {'max_policy_requests': 1, 'window_chars': 256},
        )
        for options in scenarios:
            with self.subTest(options=options):
                client = FakeDecider()
                extracted, errors = analyze_batch((value,), client, **options)

                self.assertFalse(extracted)
                self.assertEqual(
                    errors,
                    {value.policy_id: 'decider_policy_analysis_budget_exceeded'},
                )
                self.assertFalse(client.requests)

    def test_provider_outage_is_not_retried_by_recursive_splitting(self):
        class OutageDecider:
            def __init__(self):
                self.request_sizes = []

            def decide(self, requests):
                self.request_sizes.append(len(requests))
                raise DeciderError('decider_provider_unavailable')

        value = policy('1', text='🙂미취업자\n' * 80)
        client = OutageDecider()
        extracted, errors = analyze_batch(
            (value,), client, window_chars=256, max_policy_requests=32,
        )

        self.assertFalse(extracted)
        self.assertEqual(errors, {value.policy_id: 'decider_provider_unavailable'})
        self.assertEqual(len(client.request_sizes), 1)
        self.assertGreater(client.request_sizes[0], 1)

    def test_cached_restart_and_changed_source(self):
        with TemporaryDirectory() as directory:
            raw = FakeDecider()
            client = CheckpointDecider(raw, Path(directory))
            value = policy('1')
            original, errors = analyze_batch((value,), client)
            self.assertFalse(errors)
            count = len(raw.requests)
            restarted, _ = analyze_batch((value,), client)
            self.assertEqual(original, restarted)
            self.assertEqual(len(raw.requests), count)
            changed = replace(value, documents=(replace(value.documents[0], text=value.documents[0].text + '\n수정'),))
            _, errors = analyze_batch((changed,), client)
            self.assertFalse(errors)
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

    def test_batch_persists_explicit_empty_raw_spans(self):
        value = policy('1')
        document = value.documents[0]
        start = document.text.index('미취업자')
        final = LocatedSpan(
            'eligibility', start, start + len('미취업자'), '미취업자', 1.0, 'source_context',
        )
        extraction = DocumentExtraction(
            document, (final,), {'analysis_mode': 'structured_first'},
            raw_spans=(), preparation={'model_requests': 1},
        )
        saved = {}

        def save(path: Path, payload: object) -> None:
            saved[path.name] = payload

        def analyze(values, client, **kwargs):
            return {value.policy_id: (extraction,)}, {}

        report = run_batch(
            (value,), FakeDecider(), root=Path('/fictional'),
            compile_policy=lambda *_: Compilation(None, ('held_for_test',), {}),
            analyze=analyze, save=save,
        )

        persisted = saved['extraction.json']['documents'][0]
        self.assertEqual(persisted['spans'][0]['method'], 'source_context')
        self.assertEqual(persisted['raw_spans'], [])
        self.assertEqual(report['counts']['held'], 1)

    def test_unpassed_or_different_model_evaluation_cannot_publish(self):
        with self.assertRaises(ValueError):
            require_evaluation({'eligible_for_publication': False}, FakeDecider().identity)
        with self.assertRaises(ValueError):
            require_evaluation({'eligible_for_publication': True, 'model': {}}, FakeDecider().identity)

    def test_explicit_empty_raw_spans_are_not_counted_as_model_recall(self):
        report, gold, extractions = evaluation_fixture(count=10)
        for extraction in extractions.values():
            extraction['documents'][0]['raw_spans'] = []

        result = evaluate_report(report, gold, extractions)

        self.assertEqual(result['totals']['predicted_characters'], 0)
        self.assertEqual(result['totals']['matched_characters'], 0)
        self.assertEqual(result['recall'], 0.0)
        self.assertEqual(result['precision'], 0.0)
        self.assertFalse(result['eligible_for_publication'])

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
        with self.assertRaisesRegex(ValueError, 'decider_evaluation_not_passed'):
            require_evaluation({**result, 'eligible_for_publication': True}, report['model'])

    def test_per_decision_sample_completeness_blocks_aggregate_pass(self):
        report, gold, extractions = evaluation_fixture(
            count=20, amount_cases=5, wrong_amount=frozenset({0}),
        )
        result = evaluate_report(report, gold, extractions)
        self.assertGreater(result['decision_accuracy'], 0.95)
        self.assertLess(result['decisions']['amount_kind']['accuracy'], 0.95)
        self.assertEqual(result['decisions']['overlap_kind']['accuracy'], 1.0)
        self.assertEqual(result['decisions']['amount_kind']['checks'], 5)
        self.assertFalse(result['eligible_for_publication'])
        with self.assertRaisesRegex(ValueError, 'decider_evaluation_not_passed'):
            require_evaluation({**result, 'eligible_for_publication': True}, report['model'])

    def test_individual_failure_stats_allow_40_percent_overall_decision_accuracy(self):
        report, gold, extractions = evaluation_fixture()
        wrong_keys = ('amount_kind', 'eligibility_kind', 'overlap_kind')
        for case in gold['cases']:
            case['documents'][0]['spans'][0]['end'] = 5
        for extraction in extractions.values():
            document = extraction['documents'][0]
            document['spans'][0]['end'] = 1
            for key in wrong_keys:
                document['decisions'][key] = 'incorrect'

        result = evaluate_report(report, gold, extractions)

        self.assertEqual(result['criteria_version'], 'decider-evaluation-v3')
        self.assertEqual(result['thresholds'], {
            'precision': 0.95, 'recall': 0.20, 'decision_accuracy': 0.40,
        })
        self.assertEqual(result['decision_accuracy'], 0.40)
        self.assertEqual(result['precision'], 1.0)
        self.assertEqual(result['recall'], 0.20)
        self.assertEqual(result['totals']['decision_checks'], 50)
        self.assertEqual(result['totals']['correct_decisions'], 20)
        for key in wrong_keys:
            self.assertEqual(result['decisions'][key], {
                'checks': 10, 'correct': 0, 'accuracy': 0.0,
            })
        self.assertEqual(result['mode'], 'strict')
        self.assertEqual(result['model_quality_gate'], 'strict')
        self.assertEqual(result['relaxed_global_gates'], [])
        self.assertTrue(result['eligible_for_publication'])
        require_evaluation(result, report['model'])
        require_evaluation(result, report['model'], mode='strict')

    def test_399_per_mille_overall_decision_accuracy_cannot_publish(self):
        report, gold, extractions = evaluation_fixture(count=200)
        checked = 0
        for extraction in extractions.values():
            decisions = extraction['documents'][0]['decisions']
            for key in decisions:
                if checked >= 399:
                    decisions[key] = 'incorrect'
                checked += 1

        result = evaluate_report(report, gold, extractions)

        self.assertEqual(result['totals']['decision_checks'], 1000)
        self.assertEqual(result['totals']['correct_decisions'], 399)
        self.assertEqual(result['decision_accuracy'], 0.399)
        self.assertEqual(result['precision'], 1.0)
        self.assertEqual(result['recall'], 1.0)
        self.assertFalse(result['eligible_for_publication'])
        with self.assertRaisesRegex(ValueError, 'decider_evaluation_not_passed'):
            require_evaluation({**result, 'eligible_for_publication': True}, report['model'])

    def test_mock_provider_cannot_qualify_even_with_perfect_metrics(self):
        report, gold, extractions = evaluation_fixture(provider='mock')
        result = evaluate_report(report, gold, extractions)
        self.assertEqual(result['totals']['correct_decisions'], result['totals']['decision_checks'])
        self.assertEqual(result['precision'], 1.0)
        self.assertEqual(result['recall'], 1.0)
        self.assertFalse(result['eligible_for_publication'])
        for mode in ('strict', 'mvp'):
            with self.subTest(mode=mode), self.assertRaisesRegex(
                ValueError, 'decider_evaluation_not_passed',
            ):
                require_evaluation({**result, 'eligible_for_publication': True},
                                   report['model'], mode=mode)

    def test_complete_reference_is_required_for_each_field_and_decision(self):
        report, gold, extractions = evaluation_fixture()
        complete = evaluate_report(report, gold, extractions)
        self.assertTrue(complete['eligible_for_publication'])
        require_evaluation(complete, report['model'])
        gold['cases'][0]['reviewed_fields'].remove('exceptions')
        incomplete = evaluate_report(report, gold, extractions)
        self.assertFalse(incomplete['eligible_for_publication'])
        with self.assertRaisesRegex(ValueError, 'decider_evaluation_not_passed'):
            require_evaluation({**incomplete, 'eligible_for_publication': True}, report['model'])
        gold['cases'][0]['reviewed_fields'] = list(KINDS)
        decisions = gold['cases'][0]['documents'][0]['decisions']
        for key in list(decisions):
            expected = decisions.pop(key)
            with self.subTest(key=key):
                incomplete = evaluate_report(report, gold, extractions)
                self.assertFalse(incomplete['eligible_for_publication'])
                with self.assertRaisesRegex(ValueError, 'decider_evaluation_not_passed'):
                    require_evaluation({**incomplete, 'eligible_for_publication': True},
                                       report['model'])
            decisions[key] = expected

    def test_evaluation_rejects_gold_source_hash_mismatch(self):
        report, gold, extractions = evaluation_fixture()
        gold['cases'][0]['input_sha256'] = '0' * 64
        for mode in ('strict', 'mvp'):
            with self.subTest(mode=mode), self.assertRaisesRegex(
                ValueError, 'decider_gold_source_mismatch',
            ):
                evaluate_report(report, gold, extractions, mode=mode)

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

    def test_approved_recall_floor_is_recorded_and_applies_at_boundary(self):
        report, gold, extractions = evaluation_fixture()
        for case in gold['cases']:
            case['documents'][0]['spans'][0]['end'] = 5
        for extraction in extractions.values():
            extraction['documents'][0]['spans'][0]['end'] = 1

        result = evaluate_report(report, gold, extractions)

        self.assertEqual(result['criteria_version'], EVALUATION_CRITERIA_VERSION)
        self.assertEqual(result['thresholds'], EVALUATION_THRESHOLDS)
        self.assertEqual(result['precision'], 1.0)
        self.assertEqual(result['recall'], 0.20)
        self.assertEqual(result['decision_accuracy'], 1.0)
        self.assertTrue(result['eligible_for_publication'])
        require_evaluation(result, report['model'])

        for case in gold['cases']:
            case['documents'][0]['spans'][0]['end'] = 6
        below_floor = evaluate_report(report, gold, extractions)
        self.assertLess(below_floor['recall'], EVALUATION_THRESHOLDS['recall'])
        self.assertFalse(below_floor['eligible_for_publication'])
        with self.assertRaisesRegex(ValueError, 'decider_evaluation_not_passed'):
            require_evaluation({**below_floor, 'eligible_for_publication': True}, report['model'])

    def test_precision_floor_still_blocks_when_recall_passes(self):
        report, gold, extractions = evaluation_fixture()
        for case in gold['cases']:
            case['documents'][0]['spans'][0]['end'] = 95
        for extraction in extractions.values():
            extraction['documents'][0]['spans'][0]['end'] = 100

        result = evaluate_report(report, gold, extractions)

        self.assertEqual(result['recall'], 1.0)
        self.assertEqual(result['precision'], 0.95)
        self.assertTrue(result['eligible_for_publication'])
        require_evaluation(result, report['model'])

        gold['cases'][0]['documents'][0]['spans'][0]['end'] = 94
        below_floor = evaluate_report(report, gold, extractions)
        self.assertEqual(below_floor['recall'], 1.0)
        self.assertEqual(below_floor['precision'], 0.949)
        self.assertFalse(below_floor['eligible_for_publication'])
        with self.assertRaisesRegex(ValueError, 'decider_evaluation_not_passed'):
            require_evaluation({**below_floor, 'eligible_for_publication': True}, report['model'])

    def test_require_evaluation_rejects_missing_or_stale_criteria(self):
        report, gold, extractions = evaluation_fixture()
        result = evaluate_report(report, gold, extractions)
        missing_criteria = dict(result)
        del missing_criteria['criteria_version']
        missing_thresholds = dict(result)
        del missing_thresholds['thresholds']

        for value in (
            missing_criteria,
            missing_thresholds,
            {**result, 'criteria_version': 'decider-evaluation-v1'},
            {**result, 'criteria_version': 'decider-evaluation-v2'},
            {**result, 'thresholds': {**result['thresholds'], 'recall': 0.95}},
            {**result, 'thresholds': {**result['thresholds'], 'decision_accuracy': 0.0}},
            {**result, 'thresholds': {**result['thresholds'], 'decision_field_accuracy': 0.95}},
        ):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError, 'decider_evaluation_not_passed',
            ):
                require_evaluation(value, report['model'])

    def test_strict_global_evaluation_gates_reject_forged_publication_flag(self):
        for change in ('sample_count', 'features'):
            report, gold, extractions = evaluation_fixture(count=9 if change == 'sample_count' else 10)
            if change == 'features':
                for case in gold['cases']:
                    case['features'].remove('exceptions')
            result = evaluate_report(report, gold, extractions)
            with self.subTest(change=change):
                self.assertFalse(result['eligible_for_publication'])
                with self.assertRaisesRegex(ValueError, 'decider_evaluation_not_passed'):
                    require_evaluation({**result, 'eligible_for_publication': True}, report['model'])

    def test_mvp_evaluation_failures_are_diagnostic_for_matching_actual_model(self):
        for provider in ('local', 'external_api'):
            report, gold, extractions = evaluation_fixture(
                count=1, provider=provider, failed_policies=frozenset({'youth:eval-0'}),
            )
            gold['coverage'] = 'critical_evidence_only'
            case = gold['cases'][0]
            case['reviewed_fields'] = []
            case['features'] = []
            del case['documents'][0]['decisions']['amount_kind']

            result = evaluate_report(report, gold, extractions, mode='mvp')

            with self.subTest(provider=provider):
                self.assertEqual(result['mode'], 'mvp')
                self.assertEqual(result['model_quality_gate'], 'diagnostic')
                self.assertCountEqual(result['relaxed_global_gates'], (
                    'precision', 'recall', 'decision_accuracy', 'gold_coverage',
                    'reference_fields_complete', 'sample_count', 'features',
                    'required_decisions', 'decision_sample_counts', 'failed_policies',
                ))
                self.assertEqual(result['totals']['failed_policies'], 1)
                self.assertEqual(result['sample_count'], 1)
                self.assertEqual(result['precision'], 0.0)
                self.assertEqual(result['recall'], 0.0)
                self.assertEqual(result['decision_accuracy'], 0.0)
                self.assertFalse(result['reference_fields_complete'])
                self.assertNotIn('amount_kind', result['decisions'])
                self.assertFalse(result['eligible_for_publication'])
                with self.assertRaisesRegex(ValueError, 'decider_evaluation_not_passed'):
                    require_evaluation(result, report['model'])
                require_evaluation(result, report['model'], mode='mvp')
                self.assertFalse(result['eligible_for_publication'])

    def test_mvp_evaluation_has_no_sample_minimum_and_requires_explicit_mode(self):
        report, gold, extractions = evaluation_fixture(count=0)
        with self.assertRaisesRegex(ValueError, 'decider_invalid_gold'):
            evaluate_report(report, gold, extractions)
        result = evaluate_report(report, gold, extractions, mode='mvp')
        self.assertEqual(result['sample_count'], 0)
        self.assertFalse(result['eligible_for_publication'])
        require_evaluation(result, report['model'], mode='mvp')
        with self.assertRaisesRegex(ValueError, 'decider_evaluation_not_passed'):
            require_evaluation(result, report['model'])

        report, gold, extractions = evaluation_fixture(count=1)
        strict_diagnostic = evaluate_report(report, gold, extractions)
        self.assertFalse(strict_diagnostic['eligible_for_publication'])
        require_evaluation(strict_diagnostic, report['model'], mode='mvp')

    def test_mvp_evaluation_rejects_mismatched_or_non_actual_model_identity(self):
        report, gold, extractions = evaluation_fixture(count=1)
        result = evaluate_report(report, gold, extractions, mode='mvp')
        for key, wrong in (
            ('id', 'fictional-model'), ('revision', 'other-revision'),
            ('questions', 'other-questions'), ('provider', 'mock'),
        ):
            different = {**report['model'], key: wrong}
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, 'decider_evaluation_not_passed'):
                    require_evaluation(result, different, mode='mvp')
                with self.assertRaisesRegex(ValueError, 'decider_evaluation_not_passed'):
                    require_evaluation({**result, 'model': different}, different, mode='mvp')
        with self.assertRaisesRegex(ValueError, 'decider_evaluation_not_passed'):
            require_evaluation(result, {**report['model'], 'provider': 'external_api'}, mode='mvp')

    def test_previous_question_version_evaluation_cannot_authorize_new_pipeline(self):
        report, gold, extractions = evaluation_fixture(count=1)
        legacy_model = {**report['model'], 'questions': 'youth-decider-v4-two-stage'}
        legacy_report = {**report, 'model': legacy_model}

        self.assertNotEqual(legacy_model['questions'], QUESTION_VERSION)
        legacy_result = evaluate_report(legacy_report, gold, extractions, mode='mvp')
        with self.assertRaisesRegex(ValueError, 'decider_evaluation_not_passed'):
            require_evaluation(legacy_result, report['model'], mode='mvp')

        with self.assertRaisesRegex(ValueError, 'decider_evaluation_not_passed'):
            require_evaluation(legacy_result, legacy_model, mode='mvp')

    def test_evaluation_rejects_unknown_validation_mode(self):
        report, gold, extractions = evaluation_fixture()
        result = evaluate_report(report, gold, extractions)
        with self.assertRaisesRegex(ValueError, 'decider_invalid_validation_mode'):
            evaluate_report(report, gold, extractions, mode='unknown')
        with self.assertRaisesRegex(ValueError, 'decider_invalid_validation_mode'):
            require_evaluation(result, report['model'], mode='unknown')

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
