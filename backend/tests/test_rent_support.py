"""Fictional parameterized source, not a stored real policy or applicant."""

from dataclasses import replace
from datetime import date
from types import SimpleNamespace
import unittest

from backend.tests.catalog_fixtures import MemoryPolicies, release_data
from benefitfinder.application.decider import DocumentExtraction, LocatedSpan
from benefitfinder.application.published_recommendations import PublishedRecommendations
from benefitfinder.api.schemas.recommendation import UserProfile
from benefitfinder.domains.youth.published_eligibility import evaluate_condition
from benefitfinder.domains.youth.published_scenario import change_published_profile
from benefitfinder.domains.youth.rent_support import assess_rent_support, rent_support_payments
from benefitfinder.infrastructure.policy_catalog_records import CatalogRecord, validate_source_text
from benefitfinder.infrastructure.policy_analysis_inputs import analysis_input_from_payload
from benefitfinder.ingestion.rent_support import rent_support_candidate, rent_support_record


def source_input():
    fields = {
        'plcyNo': '999000123', 'plcyNm': '[가상] 청년월세 특별지원(2차)',
        'lclsfNm': '주거', 'mclsfNm': '주거비지원', 'rgtrInstCdNm': '가상 기관',
        'sprtTrgtMinAge': '18', 'sprtTrgtMaxAge': '36', 'zipCd': '99999',
        'aplyYmd': '20260101 ~ 20261231', 'earnCndSeCd': '0043003',
        'earnMinAmt': '0', 'earnMaxAmt': '0',
        'earnEtcCn': '청년가구 : 기준 중위소득 65% 이하이며 재산가액이 1.11억원 이하, 원가구 : 기준 중위소득 105% 이하이면서 재산가액이 4.2억원 이하',
        'addAplyQlfcCndCn': '연령조건 : 만 18~36세\n청약통장가입\n부모로부터 독립한 무주택 청년가구\n청년가구 : 기준 중위소득 65% 이하이며 재산가액이 1.11억원 이하\n원가구 : 기준 중위소득 105% 이하이면서 재산가액이 4.2억원 이하\n다만, 31세 이상이거나 혼인 등 부모와 생계를 달리하는 청년은 청년 본인가구의 소득·재산만 확인',
        'plcySprtCn': '실제 납부하는 임대료 범위 내에서 월 최대 15만원씩 12개월 동안 매월 분할 지원\n주거급여수급자도 월세지원 한도액 15만원에서 주거급여액을 차감하고 지원\n지급기간 : 2026년 3월 ~ 2027년 12월',
        'etcMttrCn': '가상 기타 제외조건은 확인이 필요합니다.',
    }
    text = '\n\n'.join(f'[{k}]\n{v}' for k, v in fields.items())
    return analysis_input_from_payload({
        'policy_id': 'youth:youthcenter:999000123',
        'policies': [{'id': 'youth:youthcenter:999000123', 'name': fields['plcyNm'], 'categories': ['housing'], 'aliases': []}],
        'documents': [{'id': 'fictional-document', 'policy_id': 'youth:youthcenter:999000123',
                       'source_url': 'https://catalog.example/rent', 'document_version': 'fictional-v1',
                       'collected_at': '2026-11-01T00:00:00+00:00', 'official': True, 'text': text}],
        'collection_sources': [{'source': 'youthcenter', 'external_id': '999000123', 'material_sha256': 'a' * 64}],
    })


def profile(**changes):
    facts = dict(youth_income_ratio_bps=6500, youth_assets_won=111_000_000,
                 original_income_ratio_bps=10500, original_assets_won=420_000_000,
                 original_household_exempt=False, subscription_account=True,
                 independent_from_parents=True, other_exclusions_clear=True,
                 monthly_rent_won=300_000, housing_benefit_rent_won=50_000,
                 requested_months=12, prior_supported_months=0, first_payment_month='2026-12')
    facts.update(changes)
    return UserProfile.model_validate({'age': 25, 'region_code': '99999',
        'domain_data': {'marital': 'single', 'housing': {'home_ownership': 'homeless'},
                        'contract': {'type': 'monthly_rent'}, 'cash_facts': {'rent_support': facts}}})


class RentSupportTest(unittest.TestCase):
    def setUp(self):
        self.value = source_input()
        self.candidate = rent_support_candidate(self.value, ())
        self.rule = self.candidate['rule']

    def test_source_parameters_not_hardcoded_and_residual_preserved(self):
        self.assertEqual(self.rule['youth_income_max_bps'], 6500)
        self.assertEqual(self.rule['original_asset_max_won'], 420_000_000)
        self.assertEqual(self.rule['parent_exempt_min_age'], 31)
        self.assertEqual(self.rule['maximum_months'], 12)
        self.assertFalse(self.candidate['publication_ready'])
        record = rent_support_record(self.value, self.candidate)
        parsed = CatalogRecord.model_validate(record)
        validate_source_text(parsed, self.value.documents[0].text)
        self.assertFalse(parsed.benefit.reviewed)
        self.assertTrue(parsed.unresolved_combination_rules)
        self.assertIsNone(parsed.payments)
        self.assertIn('가상 기타 제외조건', str(record))

    def test_each_inclusive_financial_boundary_and_one_unit_over(self):
        for field, condition, limit in (
            ('youth_income_ratio_bps', 'youth_income', 6500),
            ('original_income_ratio_bps', 'original_income', 10500),
            ('youth_assets_won', 'youth_assets', 111_000_000),
            ('original_assets_won', 'original_assets', 420_000_000),
        ):
            for value, expected in ((limit - 1, 'met'), (limit, 'met'), (limit + 1, 'not_met'), (None, 'needs_review')):
                with self.subTest(field=field, value=value):
                    self.assertEqual(assess_rent_support(profile(**{field: value}), self.rule)[condition], expected)

    def test_age_marriage_and_explicit_other_exemptions_only_skip_original(self):
        p = profile(original_income_ratio_bps=99999, original_assets_won=999_000_000)
        self.assertEqual(assess_rent_support(p, self.rule)['original_income'], 'not_met')
        p.age = 30
        self.assertEqual(assess_rent_support(p, self.rule)['original_income'], 'not_met')
        p.age = 31
        self.assertEqual(assess_rent_support(p, self.rule)['original_income'], 'met')
        p.domain_data.cash_facts.rent_support.youth_assets_won += 1
        self.assertEqual(assess_rent_support(p, self.rule)['youth_assets'], 'not_met')
        p.age = 25
        p.domain_data.marital = 'married'
        self.assertEqual(assess_rent_support(p, self.rule)['original_assets'], 'met')
        p.domain_data.marital = 'marrying_soon'
        self.assertEqual(assess_rent_support(p, self.rule)['original_assets'], 'not_met')
        p.domain_data.cash_facts.rent_support.original_household_exempt = None
        self.assertEqual(assess_rent_support(p, self.rule)['original_assets'], 'needs_review')
        p.domain_data.cash_facts.rent_support.original_household_exempt = True
        self.assertEqual(assess_rent_support(p, self.rule)['original_assets'], 'met')

    def approved_policy(self):
        record = rent_support_record(self.value, self.candidate)
        # Fictional review fixture only. Compilation never grants these flags.
        record['benefit']['reviewed'] = True
        record['unresolved_combination_rules'] = False
        record['benefit']['virtual_data'] = True
        return record

    def test_actual_rent_cap_then_deduction_zero_floor_and_unknown(self):
        policy = MemoryPolicies(self.approved_policy()).policies[0]
        for rent, benefit, expected in ((300000, 50000, 100000), (100000, 50000, 50000),
                                         (300000, 150000, 0), (300000, 150001, 0),
                                         (300000, 0, 150000), (150001, 149999, 1)):
            with self.subTest(rent=rent, benefit=benefit):
                payments = rent_support_payments(profile(monthly_rent_won=rent, housing_benefit_rent_won=benefit), policy, date(2026, 11, 1))
                self.assertEqual(payments, tuple((m, expected) for m in range(1, 13)))
        self.assertIsNone(rent_support_payments(profile(housing_benefit_rent_won=None), policy, date(2026, 11, 1)))
        self.assertIsNone(rent_support_payments(profile(first_payment_month=None), policy, date(2026, 11, 1)))
        self.assertIsNone(rent_support_payments(profile(first_payment_month='2027-02'), policy, date(2026, 11, 1)))
        self.assertEqual(assess_rent_support(profile(first_payment_month='2027-02'), self.rule)['payment_period'], 'not_met')
        self.assertIsNone(rent_support_payments(profile(prior_supported_months=1), policy, date(2026, 11, 1)))

    def test_published_path_uses_dynamic_amount_and_never_the_cap_schedule(self):
        repository = MemoryPolicies(self.approved_policy(), release_data())
        service = PublishedRecommendations(repository, evaluate_condition,
                    today=lambda: date(2026, 11, 1), resolve_payments=rent_support_payments)
        request = SimpleNamespace(domain='youth', profile=profile())
        result = service(request)
        key = self.value.policy_id
        self.assertEqual(result['eligibility'][key]['status'], 'met')
        self.assertEqual(result['results']['all']['pv_off']['amounts'][key], 1_200_000)
        self.assertEqual(result['catalog']['payments'][key][0], (1, 100000))
        request.profile = profile(other_exclusions_clear=None)
        result = service(request)
        self.assertEqual(result['eligibility'][key]['status'], 'needs_review')
        self.assertNotIn(key, result['catalog']['payments'])
        # Without a resolver a dynamic policy cannot silently use its cap as cash.
        fallback = PublishedRecommendations(repository, evaluate_condition, today=lambda: date(2026, 11, 1))
        self.assertNotIn(key, fallback(SimpleNamespace(domain='youth', profile=profile()))['catalog']['payments'])

    def test_income_and_independence_scenarios_invalidate_assessed_facts(self):
        for scenario in ('income_up', 'employed', 'independent'):
            before, after, _, _ = change_published_profile(profile(), scenario, None,
                rules=tuple(self.candidate['conditions']))
            self.assertEqual(before.domain_data.cash_facts.rent_support.youth_income_ratio_bps, 6500)
            self.assertIsNone(after.domain_data.cash_facts.rent_support.youth_income_ratio_bps)
            self.assertEqual(assess_rent_support(after, self.rule)['youth_income'], 'needs_review')

    def test_http_bootstrap_uses_dynamic_resolver(self):
        from fastapi.testclient import TestClient
        from benefitfinder.bootstrap import create_app
        from backend.tests.test_published_catalog import validator
        repository = MemoryPolicies(self.approved_policy())
        app = create_app(policy_repository=repository)
        with TestClient(app) as client:
            response = client.post('/api/v1/recommendations', json={
                'domain': 'youth', 'profile': profile().model_dump(mode='json')})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        validator('recommendation-response.schema.json').validate(body)
        item = body['results']['all']['pv_off']['fallback_list'][0]
        self.assertEqual(item['amount_in_period_won'], 1_200_000)

    def test_dynamic_record_rejects_fixed_cap_payments_and_missing_parameters(self):
        record = self.approved_policy()
        record['payments'] = [{'month': 1, 'amount_won': 150000}]
        with self.assertRaisesRegex(ValueError, 'dynamic_cap'):
            CatalogRecord.model_validate(record)
        record = self.approved_policy()
        del record['conditions'][3]['rent_support']['youth_income_max_bps']
        with self.assertRaises(ValueError):
            CatalogRecord.model_validate(record)

    def test_model_triggered_scalar_correction_requires_exact_source_and_review(self):
        doc = self.value.documents[0]
        text = doc.text.replace('[sprtTrgtMaxAge]\n36', '[sprtTrgtMaxAge]\n39')
        value = replace(self.value, documents=(replace(doc, text=text),))
        doc = value.documents[0]
        quote = '만 18~36세'
        start = text.index(quote)
        span = LocatedSpan('eligibility', start, start + len(quote), quote, .99)
        extraction = DocumentExtraction(doc, (span,), {}, (span,))
        candidate = rent_support_candidate(value, (extraction,))
        update = next(u for u in candidate['field_updates'] if u['field'] == 'sprtTrgtMaxAge')
        self.assertEqual((update['before'], update['after'], update['trigger'], update['status']), (39, 36, 'decider', 'needs_review'))
        self.assertEqual(candidate['conditions'][0]['maximum'], 36)
        bad = replace(extraction, raw_spans=(replace(span, text='가짜 인용'),))
        self.assertIsNone(rent_support_candidate(value, (bad,)))

    def test_contradictory_household_copy_not_compiled(self):
        doc = self.value.documents[0]
        text = doc.text.replace('65%', '66%', 1)
        value = replace(self.value, documents=(replace(doc, text=text),))
        self.assertIsNone(rent_support_candidate(value, ()))


if __name__ == '__main__':
    unittest.main()
