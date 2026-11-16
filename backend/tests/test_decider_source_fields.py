"""Structured fields and residual rules retain independent source evidence."""

from dataclasses import replace
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from benefitfinder.application.decider import Compilation
from benefitfinder.jobs.decider_batch import run_batch

from benefitfinder.ai.decider_extraction import analyze_batch, contextualize_spans
from benefitfinder.application.decider import LocatedSpan
from benefitfinder.ingestion.decider_source_fields import prepare_source_fields
from benefitfinder.ingestion.decider_normalization import compile_policy
from backend.tests.test_decider_normalization import case


class NoInference:
    identity = {'provider': 'mock', 'questions': 'structured-first-test'}
    def decide(self, requests):
        raise AssertionError('structured-only input must not invoke a model')


class EmptyLocate:
    def decide(self, requests):
        return [{'answers': {kind: {'spans': []} for kind in request['questions']}}
                for request in requests]


class SourcePreparationTest(unittest.TestCase):
    def test_zeros_are_values_and_never_infer_unrestricted_income(self):
        plan = prepare_source_fields('[earnCndSeCd]\n0043003\n\n[earnMaxAmt]\n0')
        self.assertEqual(plan.structured['earnMaxAmt']['value'], 0)
        self.assertEqual(plan.structured['earnCndSeCd']['meanings'], ['기타'])
        self.assertNotEqual(plan.structured['earnCndSeCd']['state'], 'unrestricted')

    def test_unknown_codes_hold_instead_of_guessing_or_paying_for_mapping(self):
        value, _, document = case()
        text = '[jobCd]\n9999999\n\n[plcySprtCn]\n가상 조건'
        value = replace(value, documents=(replace(document, text=text),))
        result, failed = analyze_batch((value,), NoInference(), prepare_source=prepare_source_fields)
        self.assertEqual(result, {})
        self.assertEqual(failed[value.policy_id], 'decider_source_preparation_requires_review')

    def test_unmapped_source_goes_to_review_without_a_model_or_publication(self):
        value, _, document = case()
        value = replace(value, documents=(replace(document, text='[jobCd]\n9999999'),))
        with TemporaryDirectory() as directory:
            report = run_batch((value,), NoInference(), root=Path(directory),
                analyze=lambda values, client, **options: analyze_batch(
                    values, client, prepare_source=prepare_source_fields, **options),
                compile_policy=lambda *_: Compilation(None, (), {}), save=lambda *_: None,
                publish=lambda *_: self.fail('unknown source cannot publish'))
        self.assertEqual(report['counts']['held'], 1)
        self.assertEqual(report['counts']['failed'], 0)

    def test_restricted_major_is_preserved_and_held_without_model_guessing(self):
        value, _, document = case()
        text = '[plcyMajorCd]\n0011005\n\n[plcySprtCn]\n가상 공학 지원'
        value = replace(value, documents=(replace(document, text=text),))
        outputs, errors = analyze_batch((value,), NoInference(), prepare_source=prepare_source_fields)
        self.assertFalse(outputs)
        self.assertEqual(errors[value.policy_id], 'decider_source_preparation_requires_review')
        major = prepare_source_fields(text).structured['plcyMajorCd']
        self.assertEqual(major['meanings'], ['공학계열'])
        self.assertEqual(major['raw'], '0011005')
        self.assertEqual(major['state'], 'hold')
        self.assertFalse(prepare_source_fields('[plcyMajorCd]\n0011009').issues)

    def test_major_restriction_cannot_pass_legacy_classifier_approval(self):
        value, extracted, _ = case(coded_fields={'plcyMajorCd': '0011005'})
        result = compile_policy(value, extracted)
        self.assertIsNone(result.payload)
        self.assertIn('source_major_requirement_requires_review', result.reasons)
        unrestricted, extraction, _ = case(coded_fields={'plcyMajorCd': '0011009'})
        self.assertIsNotNone(compile_policy(unrestricted, extraction).payload)

    def test_raw_audit_detects_a_condition_omitted_before_text_routing(self):
        from unittest.mock import patch
        from scripts.verify_structured_analysis import audit_raw_conditions

        raw = [{'jobCd': '0013010', 'plcyMajorCd': '0011005'}]
        with patch('scripts.verify_structured_analysis.project_youthcenter_source',
                   return_value='[jobCd]\n0013010'):
            result = audit_raw_conditions(raw)
        self.assertFalse(result['all_preserved'])
        self.assertEqual(result['missing_field_counts'], {'plcyMajorCd': 1})

    def test_unknown_scalar_flag_is_rejected_before_model_spending(self):
        value, _, document = case()
        text = '[sprtTrgtAgeLmtYn]\nX\n\n[plcySprtCn]\n가상 지원 조건'
        value = replace(value, documents=(replace(document, text=text),))
        outputs, errors = analyze_batch((value,), NoInference(), prepare_source=prepare_source_fields)
        self.assertFalse(outputs)
        self.assertEqual(errors[value.policy_id], 'decider_source_preparation_requires_review')
        self.assertIn('source_flag_invalid:sprtTrgtAgeLmtYn', prepare_source_fields(text).issues)

    def test_actual_job_wires_structured_preparation_and_configured_limits(self):
        from unittest.mock import patch
        from benefitfinder.jobs import __main__ as jobs

        value, _, document = case()
        value = replace(value, documents=(replace(document, text='[jobCd]\n0013010'),))

        def execute(values, client, **options):
            return options['analyze'](values, client, batch_size=1, window_chars=900)

        with patch.object(jobs, 'run_decider_batch', side_effect=execute):
            outputs, errors = jobs._run_decider_values((value,), NoInference(), Path('/tmp/unused'))
        self.assertFalse(errors)
        self.assertEqual(outputs[value.policy_id][0].preparation['model_requests'], 0)
        self.assertEqual(outputs[value.policy_id][0].preparation['structured']['jobCd']['raw'], '0013010')

    def test_invalid_scalar_numbers_and_duplicate_headers_are_not_overwritten(self):
        plan = prepare_source_fields('[earnMaxAmt]\n오천\n\n[jobCd]\n0013003\n\n[jobCd]\n0013010')
        self.assertIn('source_number_invalid:earnMaxAmt', plan.issues)
        self.assertIn('source_field_duplicated', plan.issues)
        self.assertEqual(plan.structured['jobCd']['raw'], '0013003')
        self.assertEqual(plan.body[0].text, '0013010')

    def test_unknown_narrative_fields_and_qualified_dates_are_not_filtered(self):
        text = '[futureRequirement]\n등록 후 6개월 경과\n\n[aplyYmd]\n예산 소진 시 마감'
        plan = prepare_source_fields(text)
        self.assertEqual([field.name for field in plan.body], ['futureRequirement', 'aplyYmd'])
        for field in plan.body:
            self.assertEqual(text[field.start:field.end], field.text)

    def test_missing_model_conditions_cannot_drop_household_or_overlap_rules(self):
        value, _, document = case(eligibility=('30세 이상은 부모 소득 확인 제외',))
        text = document.text + ('\n\n[earnEtcCn]\n가상 청년가구 중위소득 60% 이하, 재산 1억원 이하'
                                '\n\n[ptcpPrpTrgtCn]\n다른 주거급여액을 차감한다.')
        value = replace(value, documents=(replace(document, text=text),))
        result, failed = analyze_batch((value,), EmptyLocate(), prepare_source=prepare_source_fields)
        self.assertFalse(failed)
        compiled = compile_policy(value, result[value.policy_id])
        self.assertIsNone(compiled.payload)
        self.assertIn('body_rules_require_review', compiled.reasons)
        bodies = {field['name']: field['text'] for field in compiled.normalized['body_fields']}
        self.assertIn('재산 1억원', bodies['earnEtcCn'])
        self.assertIn('부모 소득 확인 제외', bodies['addAplyQlfcCndCn'])
        self.assertIn('차감', bodies['ptcpPrpTrgtCn'])
        self.assertIsNone(compiled.normalized['consistent'])
        self.assertIsNone(compiled.normalized['readiness'])

    def test_sparse_income_selection_retains_household_and_proviso_context(self):
        text = ('[earnEtcCn]\n가상 청년가구 중위소득 60% 이하, 원가구 100% 이하. '
                '다만 혼인 시 부모 소득 제외')
        plan = prepare_source_fields(text)
        start = text.index('60%')
        raw = LocatedSpan('eligibility', start, start + 3, '60%', 0.9)
        expanded = contextualize_spans(text, [raw], plan)
        self.assertEqual(expanded[0].text, plan.body[0].text)
        self.assertEqual(expanded[0].method, 'source_context')
        self.assertIn('원가구 100%', expanded[0].text)
        self.assertIn('다만 혼인', expanded[0].text)
        self.assertEqual(raw.text, '60%')

    def test_a_model_cannot_tamper_with_code_read_fields_or_body_inventory(self):
        value, _, _ = case()
        result, failed = analyze_batch((value,), EmptyLocate(), prepare_source=prepare_source_fields)
        self.assertFalse(failed)
        extraction = result[value.policy_id][0]
        changed = replace(extraction, preparation={**extraction.preparation, 'body_fields': []})
        compiled = compile_policy(value, (changed,))
        self.assertIsNone(compiled.payload)
        self.assertIn('source_preparation_mismatch', compiled.reasons)


if __name__ == '__main__':
    unittest.main()
