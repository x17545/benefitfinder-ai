"""Fictional source facts; no model and no actual notice text."""

from dataclasses import replace
from datetime import date, datetime, timezone
import unittest

from benefitfinder.application.policy_analysis import AnalysisPolicy, AnalysisSource, PolicyAnalysisInput
from benefitfinder.application.policy_rules import SourceDocument
from benefitfinder.application.region_options import DistrictOption, RegionOption
from benefitfinder.infrastructure.policy_catalog_records import CatalogRecord, validate_source_text
from benefitfinder.ingestion.official_fixed_grants import compile_official_fixed_grant


def sample(text: str | None = None) -> PolicyAnalysisInput:
    source = text or (
        '[가상 예제] 주거안정 지원\nㅇ (지원금액) 12만원 (생애1회)\n'
        'ㅇ (신청기간) 2026. 11. 1. ~ 2026. 12. 31.\n'
        'ㅇ (지급방법) 매월 말까지 접수 → 다음달 20일 이내 개인 계좌 지급\n'
        '□ 자격요건\n가상광역시에 주민등록이 되어있는 자\n'
        '과거 가상 이주비를 받은 자는 제외\n□ 기타 유의사항\n'
        '결정 취소나 서류 미제출이면 지원 보류\n'
    )
    document = SourceDocument('fictional-official', 'youth:1001',
        'https://example.test/notice.hwp', 'official-v1:' + 'a' * 64,
        datetime(2026, 11, 16, tzinfo=timezone.utc), source, True)
    return PolicyAnalysisInput('youth:1001', (document,),
        (AnalysisPolicy('youth:1001', '가상 주거정착 지원', ('housing',)),),
        (AnalysisSource('official_documents', 'fictional-official', 'a' * 64),))


class OfficialFixedGrantTest(unittest.TestCase):
    def test_fixed_once_and_explicit_timing_keep_complete_conditions(self):
        value = sample()
        region = RegionOption('99', '가상광역시', (DistrictOption('99110', '가상구'),))
        result = compile_official_fixed_grant(value, as_of=date(2026, 11, 16), regions=(region,))
        record = CatalogRecord.model_validate(result.payload)
        validate_source_text(record, value.documents[0].text)
        self.assertTrue(record.benefit.reviewed)
        self.assertEqual([(p.month, p.amount_won) for p in record.payments], [(1, 120_000)])
        self.assertTrue(record.benefit.lifetime_once)
        self.assertEqual(record.benefit.conflicts, [])
        manual = [c for c in record.conditions if c.kind == 'manual']
        quotes = [e.quote for c in record.benefit.eligibility_conditions
                  if c.id.startswith('official_terms_') for e in c.evidence]
        self.assertTrue(manual)
        self.assertEqual(''.join(quotes), value.documents[0].text)
        self.assertIn('과거 가상 이주비', ''.join(quotes))
        self.assertEqual(record.conditions[-1].values, ['99'])
        self.assertEqual(result.normalized['ai_review_calls'], 0)

    def test_cap_is_not_a_fixed_grant(self):
        value = sample()
        value = replace(value, documents=(replace(value.documents[0], text=value.documents[0].text.replace('12만원', '최대 12만원')),))
        result = compile_official_fixed_grant(value, as_of=date(2026, 11, 16))
        self.assertIsNone(result.payload)
        self.assertIn('fixed_grant_amount_not_fixed', result.reasons)

    def test_unresolved_overlap_in_terms_holds_fixed_grant(self):
        source = sample().documents[0].text
        value = sample(source + '가상 사업과 동시 수혜 불가\n다만 별도 기준 적용\n')
        result = compile_official_fixed_grant(value, as_of=date(2026, 11, 16))
        self.assertIsNone(result.payload)
        self.assertEqual(result.reasons, ('fixed_grant_overlap_unresolved',))

    def test_payment_sequence_does_not_invent_a_payment_month(self):
        value = sample()
        source = value.documents[0].text.replace('매월 말까지 접수 → 다음달 20일 이내 개인 계좌 지급', '심사 후 계좌로 지급')
        value = replace(value, documents=(replace(value.documents[0], text=source),))
        result = compile_official_fixed_grant(value, as_of=date(2026, 11, 16))
        self.assertIsNone(result.payload)
        self.assertIn('fixed_grant_payment_timing_unknown', result.reasons)

    def test_multiple_grant_lines_are_not_collapsed(self):
        value = sample()
        value = replace(value, documents=(replace(value.documents[0], text=value.documents[0].text + '\nㅇ (지원금액) 5만원 (생애1회)'),))
        result = compile_official_fixed_grant(value, as_of=date(2026, 11, 16))
        self.assertIsNone(result.payload)
        self.assertIn('fixed_grant_amount_line_ambiguous', result.reasons)
