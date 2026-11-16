"""Fictional fixtures for offline source-fact publication compilation."""

from dataclasses import replace
from datetime import date, datetime, timezone
import unittest

from benefitfinder.application.policy_analysis import AnalysisPolicy, AnalysisSource, PolicyAnalysisInput
from benefitfinder.application.policy_rules import SourceDocument
from benefitfinder.application.region_options import DistrictOption, RegionOption
from benefitfinder.infrastructure.ai_catalog_review import project_youthcenter_source
from benefitfinder.infrastructure.policy_catalog_records import CatalogRecord, validate_source_text
from benefitfinder.ingestion.source_cash import compile_source_cash, compile_official_cash_component


ID = "20260101000000000099"
REGIONS = (RegionOption("11", "가상도", (DistrictOption("11110", "가상시"), DistrictOption("11200", "다른시"))),)
TODAY = date(2026, 11, 16)


def sample(**changes):
    fields = {
        "plcyNo": ID, "plcyNm": "가상 청년 구직수당", "lclsfNm": "일자리", "mclsfNm": "취업",
        "plcySprtCn": "매월 12만원을 6개월 동안 지급. 신청일 기준 다음 달부터 지급",
        "aplyPrdSeCd": "0057002",
        "zipCd": "11110", "jobCd": "0013003", "sprtTrgtMinAge": "19", "sprtTrgtMaxAge": "39",
        "plcyPvsnMthdCd": "0042006", "ptcpPrpTrgtCn": "가상 타 사업 참여자는 신청 전 확인",
    } | changes
    pid = "youth:youthcenter:" + ID
    doc = SourceDocument("youthcenter:" + ID, pid,
        "https://www.youthcenter.go.kr/youthPolicy/ythPlcyTotalSearch/ythPlcyDetail/" + ID,
        "sha256:" + "a" * 64, datetime(2026, 11, 16, tzinfo=timezone.utc), project_youthcenter_source(fields), True)
    return PolicyAnalysisInput(pid, (doc,), (AnalysisPolicy(pid, fields["plcyNm"], ("employment",)),),
                               (AnalysisSource("youthcenter", ID, "a" * 64),))


class SourceCashTest(unittest.TestCase):
    def test_monthly_source_facts_publish_with_preserved_conditions(self):
        value = sample()
        compiled = compile_source_cash(value, as_of=TODAY, regions=REGIONS)
        self.assertIsNotNone(compiled.payload, compiled.reasons)
        record = CatalogRecord.model_validate(compiled.payload)
        validate_source_text(record, value.documents[0].text)
        self.assertEqual([(p.month, p.amount_won) for p in record.payments], [(n, 120000) for n in range(1, 7)])
        self.assertTrue(record.benefit.source.document_version.endswith(":source-facts-v1"))
        self.assertEqual(record.benefit.application.status, "open")
        self.assertIsNone(record.benefit.application.ends_on)
        self.assertEqual(compiled.normalized["ai_review_calls"], 0)
        self.assertIn("가상 타 사업", " ".join(c.description for c in record.benefit.eligibility_conditions))
        self.assertNotIn("actual_calendar_schedule_unknown", record.benefit.benefit_amount.amount_text)

    def test_fixed_monthly_amount_without_first_payment_month_is_not_published(self):
        for proof in ("", " 선정일 기준 다음 달부터 지급"):
            with self.subTest(proof=proof):
                value = sample(plcySprtCn="매월 12만원을 6개월 동안 지급." + proof)
                compiled = compile_source_cash(value, as_of=TODAY, regions=REGIONS)
                self.assertIsNone(compiled.payload)
                self.assertEqual(compiled.reasons, ("source_cash_payment_start_unknown_or_unsupported",))

    def test_overlap_outside_the_payment_fields_blocks_publication(self):
        for restriction in (
            "국민취업지원제도와 중복 수급 불가",
            "기존 수당과 동시 수혜 제한. 다만 일부 참여자 예외",
        ):
            with self.subTest(restriction=restriction):
                value = sample(ptcpPrpTrgtCn=restriction)
                compiled = compile_source_cash(value, as_of=TODAY, regions=REGIONS)
                self.assertIsNone(compiled.payload)
                self.assertEqual(compiled.reasons, ("source_cash_overlap_requires_review",))

    def test_title_whitespace_is_preserved_for_source_identity(self):
        value = sample(plcyNm="가상 수당 ")
        compiled = compile_source_cash(value, as_of=TODAY, regions=REGIONS)
        self.assertEqual(compiled.payload["benefit"]["title"], "가상 수당 ")

    def test_cap_is_not_converted_to_fixed_payment(self):
        compiled = compile_source_cash(sample(plcySprtCn="월 최대 12만원, 6개월 지원"), as_of=TODAY, regions=REGIONS)
        self.assertIsNone(compiled.payload)

    def test_rent_caps_require_dynamic_calculation(self):
        compiled = compile_source_cash(sample(plcyNm="가상 청년 월세 지원", plcySprtCn="월 12만원 6개월 지급"),
                                       as_of=TODAY, regions=REGIONS)
        self.assertIsNone(compiled.payload)

    def test_employer_reimbursement_is_not_a_personal_grant(self):
        compiled = compile_source_cash(sample(plcySprtCn="상시 근로자 3인 이상 중소기업에 군민 신규채용시 1인당 월 70만원씩 12개월 지급"),
                                       as_of=TODAY, regions=REGIONS)
        self.assertIsNone(compiled.payload)
        self.assertIn("source_cash_employer_recipient", compiled.reasons)

    def test_unknown_region_does_not_become_nationwide(self):
        compiled = compile_source_cash(sample(zipCd="99999"), as_of=TODAY, regions=REGIONS)
        self.assertIsNone(compiled.payload)
        self.assertIn("source_cash_residence_not_executable", compiled.reasons)

    def test_savings_deposit_is_not_cash_available_during_support(self):
        compiled = compile_source_cash(sample(plcyNm="가상 근로자 재형저축 사업",
            plcySprtCn="근로자 1인당 월 25만원 5년간 지원"), as_of=TODAY, regions=REGIONS)
        self.assertIsNone(compiled.payload)
        self.assertIn("source_cash_non_grant_or_savings", compiled.reasons)

    def test_mixed_employer_and_personal_components_count_only_named_personal_part(self):
        value = sample(plcySprtCn=(
            "청년 근로자 : 임금격차지원금(월 50만원, 신청일 기준 익월부터 지급), "
            "일자리 창출 중소기업 : 채용지원금(인원수에 따라 차등) 지급"
        ))
        compiled = compile_source_cash(value, as_of=TODAY, regions=REGIONS)
        record = CatalogRecord.model_validate(compiled.payload)
        validate_source_text(record, value.documents[0].text)
        self.assertEqual([(p.month, p.amount_won) for p in record.payments], [(1, 500000)])
        self.assertIn("부분 계산(임금격차지원금)", record.benefit.benefit_amount.amount_text)
        descriptions = " ".join(c.description for c in record.benefit.eligibility_conditions)
        self.assertIn("채용지원금", descriptions)

    def test_component_selection_does_not_promote_reimbursement_caps(self):
        value = sample(plcySprtCn="청년 근로자 : 임금격차지원금(월 최대 50만원), 기업지원금 차등 지급")
        compiled = compile_source_cash(value, as_of=TODAY, regions=REGIONS)
        self.assertIsNone(compiled.payload)

    def test_explicit_monthly_side_benefit_keeps_source_qualifications(self):
        value = sample(plcySprtCn=("자립물품 구입비 지원(50만원, 1회), "
            "주거 생활비 지원(월 10만원, 12개월, 신청일 기준 익월부터 지급)"),
            ptcpPrpTrgtCn="가상 보호종료 청년만 신청 가능")
        compiled = compile_source_cash(value, as_of=TODAY, regions=REGIONS)
        record = CatalogRecord.model_validate(compiled.payload)
        validate_source_text(record, value.documents[0].text)
        self.assertEqual(record.benefit.benefit_amount.max_total_won, 1200000)
        self.assertIn("보호종료", " ".join(c.description for c in record.benefit.eligibility_conditions))

    def test_first_business_year_component_keeps_one_month_and_does_not_count_loan(self):
        value = sample(plcyNm="가상 청년 영농정착 지원", plcySprtCn=(
            "대상: 독립경영 3년 이하 청년농업인\n"
            "영농정착지원금 지급: 독립경영 1년차는 월 110만원 (신청일 기준 익월부터 지급), 2년차 월 100만원, "
            "3년차 월 90만원 지급\n육성자금 대출 최대 5억원 별도"
        ))
        compiled = compile_source_cash(value, as_of=TODAY, regions=REGIONS)
        record = CatalogRecord.model_validate(compiled.payload)
        validate_source_text(record, value.documents[0].text)
        self.assertEqual([(p.month, p.amount_won) for p in record.payments], [(1, 1100000)])
        self.assertIn("1년차", record.benefit.benefit_amount.amount_text)
        self.assertIn("대출", " ".join(c.description for c in record.benefit.eligibility_conditions))

    def test_first_year_cap_remains_a_cap(self):
        value = sample(plcyNm="가상 청년 영농정착 지원",
            plcySprtCn="정착지원금 1년차 월 최대 110만원, 2년차 월 최대 100만원")
        compiled = compile_source_cash(value, as_of=TODAY, regions=REGIONS)
        self.assertIsNone(compiled.payload)

    def compile_official_component(self, payment, tail):
        value = sample()
        text = f"[가상 공고] 지원대상 확인\n{payment}\n{tail}"
        start = text.index(payment)
        end = start + len(payment)
        doc = replace(value.documents[0], text=text, document_version="official-v1:" + "b" * 64)
        value = replace(value, documents=(doc,), collection_sources=(AnalysisSource("official_documents", "fixture", "b" * 64),))
        compiled = compile_official_cash_component(value, component_span=(start, end),
            application={"status": "open", "starts_on": None, "ends_on": None, "url": None},
            provider="가상 기관", component_description="기본수당만 계산, 연장·소득 조건 확인")
        return compiled, text

    def test_official_component_keeps_all_exceptions(self):
        compiled, text = self.compile_official_component(
            "기본수당 매월 12만원을 6개월 동안 지급. 접수일 기준 익월부터 지급",
            "연장 시 총액 동일, 소득 신고 의무",
        )
        record = CatalogRecord.model_validate(compiled.payload)
        validate_source_text(record, text)
        quotes = [e.quote for c in record.benefit.eligibility_conditions
                  if c.id.startswith("official_source_terms_") for e in c.evidence]
        self.assertEqual("".join(quotes), text)
        self.assertEqual(record.benefit.benefit_amount.max_total_won, 720000)

    def test_official_component_checks_payment_start_and_overlap_outside_the_selection(self):
        payment = "기본수당 매월 12만원을 6개월 동안 지급"
        for start_proof, tail, reason in (
            ("", "소득 신고 의무", "source_cash_payment_start_unknown_or_unsupported"),
            (". 접수일 기준 익월부터 지급", "다른 가상 수당과 동시 수혜 불가",
             "source_cash_overlap_requires_review"),
        ):
            with self.subTest(reason=reason):
                compiled, _ = self.compile_official_component(payment + start_proof, tail)
                self.assertIsNone(compiled.payload)
                self.assertEqual(compiled.reasons, (reason,))
