"""Fictional API projections verify pending preparation without DB or inference."""

from dataclasses import replace
from datetime import date, datetime, timezone
import unittest

from benefitfinder.application.decider import Compilation, DocumentExtraction, LocatedSpan
from benefitfinder.application.policy_analysis import AnalysisPolicy, AnalysisSource, PolicyAnalysisInput
from benefitfinder.application.policy_rules import SourceDocument
from benefitfinder.infrastructure.ai_catalog_review import project_youthcenter_source
from benefitfinder.infrastructure.policy_catalog_records import CatalogRecord, validate_source_text
from benefitfinder.infrastructure.unreviewed_catalog import validate_unreviewed
from benefitfinder.ingestion.decider_pending import compile_pending_policy, compile_pending_source


EXTERNAL_ID = "1001"
MATERIAL = "a" * 64
CHECKED_AT = datetime(2026, 11, 16, 1, tzinfo=timezone.utc)


def case(
    *, fields: dict | None = None, decisions: dict | None = None,
    policy_id: str = "youth:youthcenter:" + EXTERNAL_ID,
) -> tuple[PolicyAnalysisInput, tuple[DocumentExtraction, ...], dict]:
    raw = {
        "plcyNo": EXTERNAL_ID, "plcyNm": "가상 청년 월세 지원", "lclsfNm": "주거",
        "plcyExplnCn": "[가상] 청년의 주거비 지원",
        "plcySprtCn": "월 최대 20만원을 지급하되 다른 지원액을 차감합니다.",
        "rgtrInstCdNm": "가상 청년센터", "aplyPrdSeCd": "0057001",
        "aplyYmd": "20261101 ~ 20261231", "plcyAplyMthdCn": "가상 누리집에서 신청",
        "bizPrdBgngYmd": "20260101", "bizPrdEndYmd": "20261231",
        "sprtTrgtAgeLmtYn": "Y", "sprtTrgtMinAge": "19", "sprtTrgtMaxAge": "34",
        "jobCd": "0013003", "schoolCd": "0049007", "zipCd": "11110,11200",
        "earnCndSeCd": "0043002", "earnMinAmt": "0", "earnMaxAmt": "3000",
        "earnEtcCn": "연 소득 3천만원 이하. 부모의 소득은 별도 공고 확인.",
        "addAplyQlfcCndCn": "신청일 기준 거주 6개월 이상. 무주택 세대주.",
        "ptcpPrpTrgtCn": "다른 주거 지원금을 받는 경우 제외.",
        "etcMttrCn": "중복 수혜와 예외는 확인이 필요합니다.",
        "aplyUrlAddr": "https://example.test/apply",
    }
    raw.update(fields or {})
    document = SourceDocument(
        "youthcenter:" + EXTERNAL_ID, policy_id,
        "https://www.youthcenter.go.kr/youthPolicy/ythPlcyTotalSearch/ythPlcyDetail/" + EXTERNAL_ID,
        "sha256:" + MATERIAL, CHECKED_AT, project_youthcenter_source(raw), official=True,
    )
    categories = tuple(
        category for label, category in (("일자리", "employment"), ("주거", "housing"))
        if label in str(raw.get("lclsfNm", "")).split(",")
    ) or ("housing",)
    value = PolicyAnalysisInput(
        policy_id, (document,),
        (AnalysisPolicy(policy_id, raw["plcyNm"], categories),),
        (AnalysisSource("youthcenter", EXTERNAL_ID, MATERIAL),),
    )
    answers = {
        "scope": "youth_employment_or_housing", "amount_kind": "cap",
        "coverage": "uncertain", "eligibility_kind": "complex", "overlap_kind": "restricted",
        "consistent": 0.4, "readiness": 0.2,
    }
    answers.update(decisions or {})
    # Only a narrow amount span is extracted; all eligibility must come from full fields.
    start = document.text.find("20만원")
    spans = (LocatedSpan("amount", start, start + 4, "20만원", 0.9),) if start >= 0 else ()
    return value, (DocumentExtraction(document, spans, answers),), raw


class DeciderPendingTest(unittest.TestCase):
    def test_source_only_import_never_invents_a_model_decision_or_verified_amount(self) -> None:
        value, _, _ = case()
        result = compile_pending_source(value)
        record = self.record(result, value)
        self.assertEqual(result.normalized['model_analysis'], 'not_run')
        self.assertEqual(record.benefit.benefit_amount.amount_status, 'unknown')
        self.assertIsNone(record.benefit.benefit_amount.amount_won)

    def test_publication_date_rechecks_an_older_source_application_period(self) -> None:
        value, extracted, _ = case(fields={'aplyYmd': '20261101 ~ 20261117'})
        result = compile_pending_policy(value, extracted, as_of=date(2026, 11, 18))
        self.assertEqual(result.payload['benefit']['application']['status'], 'closed')

    def record(self, result: Compilation, value: PolicyAnalysisInput) -> CatalogRecord:
        self.assertIsInstance(result, Compilation)
        self.assertEqual(result.reasons, ())
        self.assertIsNotNone(result.payload)
        record = CatalogRecord.model_validate(result.payload)
        validate_source_text(record, value.documents[0].text)
        validate_unreviewed(record, value.documents[0].text)
        self.assertEqual(result.normalized["status"], "pending_prepared")
        self.assertTrue(result.normalized["pending_ready"])
        self.assertFalse(result.normalized["calculation_ready"])
        self.assertFalse(record.benefit.reviewed)
        self.assertIsNone(record.payments)
        self.assertEqual(record.payment_evidence, [])
        self.assertTrue(record.unresolved_combination_rules)
        self.assertEqual(record.benefit.conflicts, [])
        self.assertIsNone(record.benefit.lifetime_once)
        self.assertTrue(all(rule.kind == "manual" for rule in record.conditions))
        for condition in record.benefit.eligibility_conditions:
            for evidence in condition.evidence:
                self.assertLessEqual(len(evidence.quote), 2500)
                start, end = map(int, evidence.location.removeprefix("chars:").split("-"))
                self.assertEqual(value.documents[0].text[start:end], evidence.quote)
        return record

    def assert_held(self, result: Compilation, reason: str) -> None:
        self.assertIsNone(result.payload)
        self.assertIn(reason, result.reasons)
        self.assertEqual(result.normalized["status"], "held")
        self.assertFalse(result.normalized["pending_ready"])
        self.assertFalse(result.normalized["calculation_ready"])

    def test_fixed_cap_and_variable_never_supply_a_verified_amount_or_schedule(self) -> None:
        for kind in ("fixed", "cap", "variable"):
            with self.subTest(kind=kind):
                value, extracted, raw = case(decisions={"amount_kind": kind})
                result = compile_pending_policy(value, extracted)
                record = self.record(result, value)
                amount = record.benefit.benefit_amount
                self.assertEqual(amount.amount_text, raw["plcySprtCn"])
                self.assertEqual(amount.amount_status, "unknown")
                for field in ("type", "amount_won", "period_months", "max_total_won", "pay_timing"):
                    self.assertIsNone(getattr(amount, field))

    def test_both_numeric_aliases_and_exact_source_provenance(self) -> None:
        for policy_id in ("youth:" + EXTERNAL_ID, "youth:youthcenter:" + EXTERNAL_ID):
            with self.subTest(policy_id=policy_id):
                value, extracted, raw = case(policy_id=policy_id)
                record = self.record(compile_pending_policy(value, extracted), value)
                self.assertEqual(record.benefit.id, policy_id)
                self.assertEqual(record.benefit.title, raw["plcyNm"])
                self.assertEqual(record.benefit.provider, raw["rgtrInstCdNm"])
                self.assertEqual(record.benefit.categories, ["housing"])
                self.assertEqual(record.benefit.source.document_version, "sha256:" + MATERIAL)
                self.assertEqual(record.benefit.source.checked_at, CHECKED_AT.isoformat())
                self.assertEqual(record.source_sha256, value.documents[0].content_sha256)
                self.assertEqual(record.collection_sources[0].material_sha256, MATERIAL)

    def test_preserves_every_present_age_income_code_and_additional_condition_field(self) -> None:
        value, extracted, raw = case()
        record = self.record(compile_pending_policy(value, extracted), value)
        conditions = {condition.id: condition for condition in record.benefit.eligibility_conditions}
        names = (
            "sprtTrgtAgeLmtYn", "sprtTrgtMinAge", "sprtTrgtMaxAge", "earnCndSeCd",
            "earnMinAmt", "earnMaxAmt", "earnEtcCn", "jobCd", "schoolCd", "zipCd",
            "addAplyQlfcCndCn", "ptcpPrpTrgtCn", "etcMttrCn", "plcySprtCn",
        )
        for name in names:
            with self.subTest(field=name):
                quotes = conditions["pending:" + name].evidence
                self.assertEqual("".join(item.quote for item in quotes), raw[name])
        self.assertIn("미취업자", conditions["pending:jobCd"].description)
        self.assertIn("대학 졸업", conditions["pending:schoolCd"].description)
        self.assertNotIn("경기도", conditions["pending:zipCd"].description)

    def test_unknown_code_is_preserved_without_inventing_a_meaning(self) -> None:
        value, extracted, _ = case(fields={"jobCd": "9999999"})
        record = self.record(compile_pending_policy(value, extracted), value)
        condition = next(c for c in record.benefit.eligibility_conditions if c.id == "pending:jobCd")
        self.assertIn("코드 의미 확인 필요", condition.description)
        self.assertNotIn("제한없음", condition.description)
        self.assertEqual(condition.evidence[0].quote, "9999999")

    def test_absent_conditions_are_not_created_even_when_model_has_no_locate_spans(self) -> None:
        value, extracted, _ = case(fields={
            "sprtTrgtAgeLmtYn": None, "sprtTrgtMinAge": None, "sprtTrgtMaxAge": None,
            "earnCndSeCd": None, "earnMinAmt": None, "earnMaxAmt": None, "earnEtcCn": None,
            "jobCd": None, "schoolCd": None, "zipCd": None, "addAplyQlfcCndCn": None,
        })
        record = self.record(compile_pending_policy(value, (replace(extracted[0], spans=()),)), value)
        ids = {condition.id for condition in record.benefit.eligibility_conditions}
        self.assertNotIn("pending:sprtTrgtMinAge", ids)
        self.assertNotIn("pending:earnMinAmt", ids)
        self.assertNotIn("pending:addAplyQlfcCndCn", ids)

    def test_long_quotes_are_split_without_losing_any_condition_or_amount_text(self) -> None:
        long_condition = "[가상] 거주 조건과 예외를 모두 확인해야 합니다.\n" * 210
        support = "월 최대 20만원 지원.\n" + "[가상] 추가 지급 조건을 확인하세요.\n" * 200
        value, extracted, _ = case(fields={
            "addAplyQlfcCndCn": long_condition, "plcySprtCn": support,
        })
        record = self.record(compile_pending_policy(value, extracted), value)
        for name, expected in (("addAplyQlfcCndCn", long_condition), ("plcySprtCn", support)):
            condition = next(c for c in record.benefit.eligibility_conditions if c.id == "pending:" + name)
            self.assertGreater(len(condition.evidence), 1)
            self.assertEqual("".join(item.quote for item in condition.evidence), expected)
        self.assertEqual(record.benefit.benefit_amount.amount_text, support)

    def test_business_other_and_uncertain_model_scope_are_held(self) -> None:
        for scope in ("business", "service", "other", "uncertain", None):
            with self.subTest(scope=scope):
                value, extracted, _ = case(decisions={"scope": scope})
                self.assert_held(compile_pending_policy(value, extracted), "pending_scope_not_youth")

    def test_none_uncertain_and_missing_amount_kind_are_held(self) -> None:
        for kind in ("none", "uncertain", "service", None):
            with self.subTest(kind=kind):
                value, extracted, _ = case(decisions={"amount_kind": kind})
                self.assert_held(compile_pending_policy(value, extracted), "pending_cash_kind_unverified")

    def test_money_in_an_income_condition_does_not_make_support_cash(self) -> None:
        value, extracted, _ = case(fields={"plcySprtCn": "[가상] 자격을 확인하여 지원합니다."})
        self.assert_held(compile_pending_policy(value, extracted), "pending_source_cash_amount_missing")

    def test_employer_and_service_source_text_override_an_incorrect_cash_classifier(self) -> None:
        for fields, reason in (
            ({"plcySprtCn": "청년을 채용한 사업주에게 월 20만원 지급."}, "pending_employer_or_business_notice"),
            ({"plcySprtCn": "20만원 상당의 무료 교육 제공."}, "pending_service_notice"),
            ({"plcySprtCn": "1인당 20만원 상당 상담 서비스를 무료로 제공합니다."}, "pending_service_notice"),
            ({"plcySprtCn": "20만원 상당의 무료 교육을 제공합니다."}, "pending_service_notice"),
            ({"plcyPvsnMthdCd": "0042002"}, "pending_service_notice"),
        ):
            with self.subTest(fields=fields):
                value, extracted, _ = case(fields=fields)
                self.assert_held(compile_pending_policy(value, extracted), reason)

    def test_original_name_categories_provider_and_external_id_are_required(self) -> None:
        for fields, reason in (
            ({"lclsfNm": "주거,교육"}, "pending_source_category_out_of_scope"),
            ({"plcyNo": "1002"}, "pending_source_policy_mismatch"),
            ({"rgtrInstCdNm": None}, "pending_provider_source_missing"),
        ):
            with self.subTest(fields=fields):
                value, extracted, _ = case(fields=fields)
                self.assert_held(compile_pending_policy(value, extracted), reason)
        value, extracted, _ = case()
        for target, reason in (
            (replace(value.policies[0], name="다른 가상 이름"), "pending_title_source_mismatch"),
            (replace(value.policies[0], categories=("employment",)), "pending_category_source_mismatch"),
        ):
            self.assert_held(compile_pending_policy(replace(value, policies=(target,)), extracted), reason)

    def test_single_document_exact_youthcenter_binding_and_successful_extraction_required(self) -> None:
        value, extracted, _ = case()
        self.assert_held(compile_pending_policy(value, ()), "pending_extraction_missing_or_invalid")
        other = replace(value.documents[0], id="supplement", text="[가상] 보충 원문")
        self.assert_held(
            compile_pending_policy(replace(value, documents=(*value.documents, other)), extracted),
            "pending_single_api_source_required",
        )
        for source in (AnalysisSource("youthcenter", "1002", MATERIAL),
                       AnalysisSource("gov24", EXTERNAL_ID, MATERIAL)):
            self.assert_held(
                compile_pending_policy(replace(value, collection_sources=(source,)), extracted),
                "pending_source_binding_mismatch",
            )
        changed = replace(extracted[0], document=replace(value.documents[0], text="[가상] 다른 원문"))
        self.assert_held(compile_pending_policy(value, (changed,)), "pending_extraction_source_mismatch")

    def test_tampered_or_nonfinite_model_spans_cannot_prepare_a_payload(self) -> None:
        value, extracted, _ = case()
        for span in (replace(extracted[0].spans[0], text="30만원"),
                     replace(extracted[0].spans[0], score=float("nan"))):
            self.assert_held(
                compile_pending_policy(value, (replace(extracted[0], spans=(span,)),)),
                "pending_extraction_span_invalid",
            )

    def test_explicit_application_dates_preserve_closed_upcoming_and_open_ranges(self) -> None:
        for raw_dates, expected, start, end in (
            ("20260101 ~ 20260131", "closed", "2026-01-01", "2026-01-31"),
            ("20261201 ~ 20261231", "upcoming", "2026-12-01", "2026-12-31"),
            ("20261101 ~ 20261231", "open", "2026-11-01", "2026-12-31"),
            ("2026.01.01 ~ 2026.01.31", "closed", "2026-01-01", "2026-01-31"),
        ):
            with self.subTest(dates=raw_dates):
                value, extracted, _ = case(fields={"aplyYmd": raw_dates})
                application = self.record(compile_pending_policy(value, extracted), value).benefit.application
                self.assertEqual((application.status, application.starts_on, application.ends_on),
                                 (expected, start, end))

    def test_missing_invalid_reversed_or_multiple_application_ranges_stay_unknown(self) -> None:
        for dates in (None, "별도 공고 확인", "20261101 ~", "20260230 ~ 20261231",
                      "20261231 ~ 20261101", "20261101~20261130,20261201~20261231"):
            with self.subTest(dates=dates):
                value, extracted, _ = case(fields={"aplyYmd": dates})
                application = self.record(compile_pending_policy(value, extracted), value).benefit.application
                self.assertEqual((application.status, application.starts_on, application.ends_on),
                                 ("unknown", None, None))

    def test_continuous_applications_never_copy_business_dates_to_application_dates(self) -> None:
        value, extracted, _ = case(fields={"aplyYmd": "상시", "aplyPrdSeCd": "0057002"})
        result = compile_pending_policy(value, extracted)
        application = self.record(result, value).benefit.application
        self.assertEqual((application.status, application.starts_on, application.ends_on),
                         ("unknown", None, None))
        self.assertEqual(result.normalized["application_reason"], "application_dates_unknown")

    def test_application_evidence_in_business_fields_cannot_replace_missing_api_application_dates(self) -> None:
        value, extracted, _ = case(fields={"aplyYmd": None, "bizPrdEtcCn": "20261101 ~ 20261231"})
        document = value.documents[0]
        text = "20261101 ~ 20261231"
        start = document.text.index(text)
        span = LocatedSpan("application", start, start + len(text), text, 1.0)
        extracted = (replace(extracted[0], spans=(*extracted[0].spans, span)),)
        application = self.record(compile_pending_policy(value, extracted), value).benefit.application
        self.assertEqual(application.status, "unknown")
        self.assertIsNone(application.starts_on)
        self.assertIsNone(application.ends_on)

    def test_open_requires_method_no_early_closure_and_consistency_with_writer(self) -> None:
        for fields in (
            {"plcyAplyMthdCn": None}, {"etcMttrCn": "예산 소진 시 조기 마감"},
            {"srngMthdCn": "회차별 모집"}, {"etcMttrCn": "별도 공고"},
            {"bizPrdEndYmd": "20261120"}, {"bizPrdEndYmd": "20260230"},
            {"aplyPrdSeCd": "9999999"}, {"aplyYmd": "2026-11-01 ~ 2026-12-31"},
        ):
            with self.subTest(fields=fields):
                value, extracted, _ = case(fields=fields)
                result = compile_pending_policy(value, extracted)
                self.assertEqual(self.record(result, value).benefit.application.status, "unknown")
                self.assertIsNotNone(result.normalized["application_reason"])

    def test_duplicate_field_headers_are_held_instead_of_dropping_original_conditions(self) -> None:
        value, extracted, _ = case()
        document = replace(value.documents[0], text=value.documents[0].text + "\n\n[jobCd]\n0013001")
        result = compile_pending_policy(
            replace(value, documents=(document,)), (replace(extracted[0], document=document),),
        )
        self.assert_held(result, "pending_source_field_duplicated_or_invalid")
