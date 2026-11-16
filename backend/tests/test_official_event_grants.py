"""Fictional official documents only; no database, network or model execution."""

from dataclasses import replace
from datetime import date, datetime, timezone
import unittest

from benefitfinder.application.policy_analysis import (
    AnalysisDocumentSource, AnalysisPolicy, AnalysisSource, PolicyAnalysisInput,
)
from benefitfinder.application.policy_rules import SourceDocument
from benefitfinder.infrastructure.policy_catalog_records import CatalogRecord, validate_source_text
from benefitfinder.ingestion.official_event_grants import compile_official_event_grant


_TEXT = (
    '[가상 공고] 가상군 청년 면접수당\n1. 사업개요\n'
    '❍ 사업기간: 2026. 2. 1.(일) ~ 12. 31.(목)\n'
    '❍ 신청기간: 2026. 2. 2.(월) ~ 12. 10.(목)\n'
    '❍ 지원내용: 1인당 연 10만원 한도 면접수당 지원(1회당 5만원, 최대 2회)\n'
    '❍ 지원금액: 1인당 연 10만원 한도 면접수당 지원(1회당 5만원, 최대 2회)\n'
    '❍ 지급일: 신청일 기준 다음 달 말일까지 지원금 지급\n'
    '매년 12월 신청분은 10일까지 접수 후 당월 지급\n'
    '❍ 대상: 지원금 신청일 기준 가상군에 주민등록을 둔 자\n'
    '면접 당시 미취업자, 현재 재직 여부와 별도로 면접 당시 자격 확인\n'
    '예산 소진 시 조기 마감\n'
    '서류와 면접 사실 및 연간 2회 수혜 이력을 확인해야 함\n'
)


def sample(text: str = _TEXT, *, policy_id: str = 'youth:1001') -> PolicyAnalysisInput:
    document = SourceDocument(
        'fictional-event', policy_id, 'https://example.test/interview.hwp',
        'official-v1:' + 'a' * 64, datetime(2026, 11, 16, tzinfo=timezone.utc), text, True,
    )
    return PolicyAnalysisInput(
        policy_id, (document,), (AnalysisPolicy(policy_id, '가상 면접수당', ('employment',)),),
        (AnalysisSource('official_documents', 'fictional-event', 'a' * 64),),
        (AnalysisDocumentSource('fictional-event', 'official_documents', 'fictional-event'),),
    )


class OfficialEventGrantTest(unittest.TestCase):
    def record(self, value: PolicyAnalysisInput, **kwargs) -> CatalogRecord:
        result = compile_official_event_grant(value, as_of=kwargs.pop('as_of', date(2026, 11, 16)),
                                              **kwargs)
        self.assertEqual(result.reasons, ())
        self.assertEqual(result.normalized['ai_review_calls'], 0)
        self.assertEqual(result.normalized['model_analysis'], 'not_run')
        self.assertTrue(result.normalized['calculation_ready'])
        record = CatalogRecord.model_validate(result.payload)
        validate_source_text(record, value.documents[0].text)
        return record

    def test_one_application_not_annual_cap_and_all_4175_chars_preserved(self):
        text = _TEXT + '가상 서류·제외·소득·기타 조건 확인\n' * 300
        value = sample(text[:4175])
        record = self.record(value, region_codes=('99110',))
        self.assertTrue(record.benefit.reviewed)
        self.assertIsNone(record.benefit.lifetime_once)
        amount = record.benefit.benefit_amount
        self.assertEqual((amount.type, amount.pay_timing), ('lump_sum', 'once'))
        self.assertEqual((amount.amount_won, amount.max_total_won), (50_000, 50_000))
        self.assertEqual([(p.month, p.amount_won) for p in record.payments], [(1, 50_000)])
        self.assertIn('1회 신청분', amount.amount_text)
        self.assertIn('1회 신청분', record.benefit.summary)
        self.assertEqual(record.benefit.application.starts_on, '2026-02-02')
        self.assertEqual(record.benefit.application.ends_on, '2026-12-10')
        self.assertEqual(record.benefit.application.status, 'open')
        terms = [c for c in record.benefit.eligibility_conditions if c.id.startswith('official_terms_')]
        self.assertEqual(len(terms), 2)
        quotes = [e.quote for condition in terms for e in condition.evidence]
        self.assertEqual(''.join(quotes), value.documents[0].text)
        for condition in terms:
            for citation in condition.evidence:
                start, end = map(int, citation.location.removeprefix('chars:').split('-'))
                self.assertEqual(citation.quote, value.documents[0].text[start:end])
                self.assertLessEqual(len(citation.quote), 2500)
        self.assertTrue(all(c.kind == 'manual' for c in record.conditions[:-1]))
        residence = record.conditions[-1]
        self.assertEqual((residence.kind, residence.field, residence.values),
                         ('selection', 'region_code', ['99110']))
        self.assertFalse(any(c.field == 'employment_status' for c in record.conditions))
        self.assertEqual(record.source_sha256, value.documents[0].content_sha256)
        self.assertEqual(record.collection_sources[0].material_sha256, 'a' * 64)
        self.assertEqual(record.benefit.source.checked_at, value.documents[0].collected_at.isoformat())

    def test_december_exception_recurs_without_a_year_and_dates_set_status(self):
        for year in (2026, 2027):
            with self.subTest(year=year):
                value = sample(_TEXT.replace('2026.', f'{year}.'))
                record = self.record(value, as_of=date(year, 12, 9))
                self.assertEqual([(p.month, p.amount_won) for p in record.payments], [(0, 50_000)])
                self.assertTrue(any('당월 지급' in e.quote for e in record.payment_evidence))
        for day, status in ((date(2026, 1, 1), 'upcoming'), (date(2026, 12, 11), 'closed')):
            with self.subTest(status=status):
                self.assertEqual(self.record(sample(), as_of=day).benefit.application.status, status)

    def test_december_exception_with_colon_and_comma_keeps_exact_evidence(self):
        for comma in (',', '，'):
            clause = f'- 12월 신청분: 10일까지 접수 후{comma} 당월 지급'
            value = sample(_TEXT.replace('매년 12월 신청분은 10일까지 접수 후 당월 지급', clause))
            for month, offset in ((11, 1), (12, 0)):
                with self.subTest(comma=comma, month=month):
                    record = self.record(value, as_of=date(2026, month, 9))
                    self.assertEqual([(p.month, p.amount_won) for p in record.payments],
                                     [(offset, 50_000)])
                    self.assertTrue(any(f'접수 후{comma} 당월 지급' in e.quote
                                        for e in record.payment_evidence))

    def test_cap_expenses_different_amounts_and_ambiguous_timing_are_held(self):
        cases = {
            'cap_only': _TEXT.replace('1회당 5만원, 최대 2회', '연간 최대 10만원'),
            'event_cap': _TEXT.replace('1회당 5만원', '1회당 최대 5만원'),
            'event_cap_suffix': _TEXT.replace('1회당 5만원', '1회당 5만원 이내'),
            'expenses': _TEXT.replace('1회당 5만원', '1회당 5만원 실비'),
            'different_duplicate': _TEXT.replace('1회당 5만원', '1회당 6만원', 1),
            'unknown_payment': _TEXT.replace('신청일 기준 다음 달 말일까지 지원금 지급', '심사 후 지원금 지급'),
            'unknown_december': _TEXT.replace('10일까지 접수 후 당월 지급', '조기 지급 예정'),
            'business_dates_only': _TEXT.replace('❍ 신청기간: 2026. 2. 2.(월) ~ 12. 10.(목)\n', ''),
            'invalid_date': _TEXT.replace('12. 10.(목)', '2. 30.(월)'),
            'reversed_date': _TEXT.replace('12. 10.(목)', '1. 10.(토)'),
            'unknown_application': _TEXT.replace('2026. 2. 2.(월) ~ 12. 10.(목)', '별도 공고'),
        }
        for case, text in cases.items():
            with self.subTest(case=case):
                result = compile_official_event_grant(sample(text), as_of=date(2026, 11, 16))
                self.assertIsNone(result.payload)
                self.assertTrue(result.reasons)
                self.assertEqual(result.normalized['status'], 'held')
                self.assertFalse(result.normalized['calculation_ready'])

    def test_only_exact_normalized_conflict_name_with_explicit_ban(self):
        exact = AnalysisPolicy('youth:2001', '국민 취업지원제도', ('employment',))
        similar = AnalysisPolicy('youth:2002', '국민취업지원제도 Ⅰ유형', ('employment',))
        alias_only = AnalysisPolicy('youth:2003', '다른 가상 사업', ('employment',), ('국민취업지원제도',))
        ban = '국민취업지원제도와 중복 수혜 불가'
        record = self.record(sample(_TEXT + ban), conflict_policies=(exact, similar, alias_only))
        self.assertEqual([c.policy_id for c in record.benefit.conflicts], ['youth:2001'])
        self.assertIn('중복 수혜 불가', record.benefit.conflicts[0].evidence[0].quote)
        self.assertEqual(self.record(sample(_TEXT + '국민취업지원제도 안내'),
                                     conflict_policies=(exact,)).benefit.conflicts, [])
        for clause, policies in (
            (ban, (similar, alias_only)),
            (ban, (exact, replace(exact, id='youth:2004'))),
            (ban + '\n다만 일부는 별도 확인', (exact,)),
            (ban + '\n※ 단, 일부는 예외', (exact,)),
            ('한시 사업에 한해 ' + ban, (exact,)),
            (ban + '하지 않음', (exact,)),
            (ban.replace('불가', '제한'), (exact,)),
            (ban + '\n동일·유사 사업 중복 지원 금지', (exact,)),
            ('제외 대상 표 | 국민취업지원제도 참여자', (exact,)),
        ):
            with self.subTest(clause=clause, policies=policies):
                result = compile_official_event_grant(sample(_TEXT + clause),
                    as_of=date(2026, 11, 16), conflict_policies=policies)
                self.assertIsNone(result.payload)
                self.assertEqual(result.reasons, ('event_grant_overlap_unresolved',))

    def test_money_units_and_caller_codes_need_actual_residence_evidence(self):
        value = sample(_TEXT.replace('1회당 5만원', '1회당 50,000원'))
        self.assertEqual(self.record(value).benefit.benefit_amount.amount_won, 50_000)
        value = sample(_TEXT.replace('지원금 신청일 기준 가상군에 주민등록을 둔 자', '가상군 면접 행사 안내'))
        record = self.record(value, region_codes=('99110',))
        self.assertTrue(all(c.kind == 'manual' for c in record.conditions))

    def test_official_bindings_and_both_numeric_api_aliases_are_preserved(self):
        for identity in ('youth:1001', 'youth:youthcenter:1001'):
            with self.subTest(identity=identity):
                value = sample(policy_id=identity)
                value = replace(value, collection_sources=(*value.collection_sources,
                                AnalysisSource('youthcenter', '1001', 'b' * 64)))
                self.assertEqual(len(self.record(value).collection_sources), 2)
        value = sample()
        cases = (
            replace(value, collection_sources=(AnalysisSource('youthcenter', '1001', 'b' * 64),),
                    document_sources=()),
            replace(value, collection_sources=(*value.collection_sources,
                    AnalysisSource('official_documents', 'another', 'c' * 64))),
            replace(value, documents=(value.documents[0], replace(value.documents[0], id='another')),
                    document_sources=()),
            replace(value, documents=(replace(value.documents[0], document_version='official-v1:' + 'c' * 64),)),
            replace(value, collection_sources=(*value.collection_sources,
                    AnalysisSource('youthcenter', '9999', 'b' * 64))),
        )
        for invalid in cases:
            with self.subTest(bindings=len(invalid.collection_sources)):
                result = compile_official_event_grant(invalid, as_of=date(2026, 11, 16))
                self.assertIsNone(result.payload)
                self.assertFalse(result.normalized['calculation_ready'])
