"""Regression tests for deterministic Decider-to-catalog compilation."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import unittest

from benefitfinder.application.decider import (
    Compilation,
    DocumentExtraction,
    LocatedSpan,
)
from benefitfinder.application.policy_analysis import (
    AnalysisPolicy,
    AnalysisSource,
    PolicyAnalysisInput,
)
from benefitfinder.application.policy_rules import SourceDocument
from benefitfinder.infrastructure.policy_catalog_records import (
    CatalogRecord,
    validate_source_text,
)
from benefitfinder.ingestion.decider_normalization import compile_policy


EXTERNAL_ID = "1001"
POLICY_ID = "youth:youthcenter:" + EXTERNAL_ID
MATERIAL_SHA256 = "a" * 64
COLLECTED_AT = datetime(2026, 11, 16, 1, tzinfo=timezone.utc)


def _span(document: SourceDocument, kind: str, text: str) -> LocatedSpan:
    start = document.text.index(text)
    return LocatedSpan(kind=kind, start=start, end=start + len(text), text=text, score=0.99)


def case(
    *,
    policy_id: str = POLICY_ID,
    amount: str = "지원 시작월부터 매월 20만원을 12개월 동안 지급합니다.",
    eligibility: tuple[str, ...] = (
        "만 19세 이상 34세 이하",
        "연 소득 3천만 원 이하",
    ),
    overlap: str | None = "다른 청년 정책과 중복 수혜 제한 없음.",
    application: str = "2026.01.01 ~ 2026.12.31",
    exception: str | None = None,
    timing: str | None = None,
    decisions: dict[str, object] | None = None,
    bindings: tuple[AnalysisSource, ...] | None = None,
) -> tuple[PolicyAnalysisInput, tuple[DocumentExtraction, ...], SourceDocument]:
    sections = [
        "[plcyNm]",
        "가상 청년 월세 지원",
        "",
        "[plcySprtCn]",
        amount,
        "",
        "[addAplyQlfcCndCn]",
        *eligibility,
        "",
        "[aplyYmd]",
        application,
        "",
        "[rgtrInstCdNm]",
        "가상 복지기관",
        "",
        "[etcMttrCn]",
    ]
    if overlap is not None:
        sections.append(overlap)
    if timing is not None:
        sections.extend(("", timing))
    if exception is not None:
        sections.extend(("", exception))
    text = "\n".join(sections)
    document = SourceDocument(
        id="youthcenter:" + EXTERNAL_ID,
        policy_id=policy_id,
        source_url="https://api.example.test/policies/" + EXTERNAL_ID,
        document_version="api-v1:different-from-publication-version",
        collected_at=COLLECTED_AT,
        text=text,
        official=True,
    )
    spans = [_span(document, "amount", amount)]
    spans.extend(_span(document, "eligibility", item) for item in eligibility)
    spans.append(_span(document, "application", application))
    if overlap is not None:
        spans.append(_span(document, "overlap", overlap))
    if exception is not None:
        spans.append(_span(document, "exceptions", exception))
    if timing is not None:
        spans.append(_span(document, "timing", timing))
    answers: dict[str, object] = {
        "coverage": "complete",
        "amount_kind": "fixed",
        "eligibility_kind": "simple",
        "overlap_kind": "none_explicit",
        "scope": "youth_employment_or_housing",
        "consistent": 0.99,
        "readiness": 1.99,
    }
    answers.update(decisions or {})
    value = PolicyAnalysisInput(
        policy_id=policy_id,
        documents=(document,),
        policies=(
            AnalysisPolicy(
                id=policy_id,
                name="가상 청년 월세 지원",
                categories=("housing",),
            ),
        ),
        collection_sources=bindings
        or (AnalysisSource("youthcenter", EXTERNAL_ID, MATERIAL_SHA256),),
    )
    extraction = DocumentExtraction(
        document=document,
        spans=tuple(spans),
        decisions=answers,
    )
    return value, (extraction,), document


class DeciderNormalizationTest(unittest.TestCase):
    def assert_catalog_payload(self, result: Compilation, document: SourceDocument) -> dict:
        self.assertEqual(result.reasons, ())
        self.assertIsNotNone(result.payload)
        record = CatalogRecord.model_validate(result.payload)
        validate_source_text(record, document.text)
        self.assertTrue(record.benefit.reviewed)
        return result.payload

    def test_complete_fixed_monthly_compiles_with_exact_provenance_and_evidence(self) -> None:
        value, extracted, document = case()
        result = compile_policy(value, extracted)
        payload = self.assert_catalog_payload(result, document)

        benefit = payload["benefit"]
        self.assertEqual(benefit["id"], POLICY_ID)
        self.assertEqual(
            benefit["source"]["url"],
            "https://www.youthcenter.go.kr/youthPolicy/ythPlcyTotalSearch/"
            "ythPlcyDetail/" + EXTERNAL_ID,
        )
        self.assertEqual(benefit["source"]["document_version"], "sha256:" + MATERIAL_SHA256)
        self.assertEqual(benefit["summary"], "지원 시작월부터 매월 20만원을 12개월 동안 지급합니다.")
        self.assertEqual(benefit["provider"], "가상 복지기관")
        self.assertEqual(benefit["benefit_amount"]["amount_won"], 200_000)
        self.assertEqual(benefit["benefit_amount"]["period_months"], 12)
        self.assertEqual(benefit["benefit_amount"]["max_total_won"], 2_400_000)
        self.assertEqual(len(payload["payments"]), 12)
        self.assertEqual(payload["unresolved_combination_rules"], False)
        self.assertEqual({rule["kind"] for rule in payload["conditions"]}, {"age", "income_max"})
        self.assertEqual(result.normalized["overlap"]["kind"], "none_explicit")

        payment = payload["payment_evidence"][0]
        amount_span = extracted[0].spans[0]
        self.assertEqual(payment["location"], f"chars:{amount_span.start}-{amount_span.end}")
        self.assertEqual(payment["quote"], amount_span.text)
        self.assertIn(amount_span.start, {item["start"] for item in result.normalized["evidence"]})

    def test_both_exact_policy_id_shapes_are_preserved(self) -> None:
        for policy_id in (POLICY_ID, "youth:" + EXTERNAL_ID):
            with self.subTest(policy_id=policy_id):
                value, extracted, document = case(policy_id=policy_id)
                result = compile_policy(value, extracted)
                payload = self.assert_catalog_payload(result, document)
                self.assertEqual(payload["benefit"]["id"], policy_id)
                self.assertEqual(payload["collection_sources"][0]["external_id"], EXTERNAL_ID)

    def test_korean_amount_units_and_boundaries_are_exact_integers(self) -> None:
        examples = (
            ("월 1,000원을 1개월 동안 지원합니다.", 1_000),
            ("월 1.5만원을 1개월 동안 지원합니다.", 15_000),
            ("월 3천만 원을 1개월 동안 지원합니다.", 30_000_000),
            ("월 1억 2,000만원을 1개월 동안 지원합니다.", 120_000_000),
        )
        for text, expected in examples:
            with self.subTest(text=text):
                value, extracted, document = case(amount=text, timing="지원 시작월부터 매월 지급합니다.")
                result = compile_policy(value, extracted)
                payload = self.assert_catalog_payload(result, document)
                self.assertEqual(payload["benefit"]["benefit_amount"]["amount_won"], expected)
                self.assertEqual(result.normalized["amount_won"], expected)

        for text in (
            "월 1,00만원을 1개월 동안 지원합니다.",
            "월 20만원~30만원을 1개월 동안 지원합니다.",
            "월 약 20만원을 1개월 동안 지원합니다.",
            "월 20만원부터 1개월 동안 지원합니다.",
        ):
            with self.subTest(rejected=text):
                value, extracted, _ = case(amount=text)
                result = compile_policy(value, extracted)
                self.assertIsNone(result.payload)
                self.assertTrue(
                    {"amount_unparseable", "amount_not_single_fixed"} & set(result.reasons)
                )

        value, extracted, _ = case(amount="월 20만원을 12개월 이상 지원합니다.",
                                   timing="지원 시작월부터 매월 지급합니다.")
        result = compile_policy(value, extracted)
        self.assertIsNone(result.payload)
        self.assertIn("duration_not_fixed", result.reasons)

    def test_cap_is_held_and_retained_in_normalized_numbers(self) -> None:
        value, extracted, _ = case(amount="월 최대 20만원을 12개월 동안 지원합니다.")
        result = compile_policy(value, extracted)
        self.assertIsNone(result.payload)
        self.assertIn("amount_cap_not_fixed", result.reasons)
        self.assertEqual(result.normalized["amount_won"], 200_000)
        self.assertNotEqual(result.normalized.get("pay_timing"), "monthly_from_start")

    def test_missing_overlap_and_payment_anchor_are_not_treated_as_allowed(self) -> None:
        value, extracted, _ = case(
            amount="지원금 100만원을 지급합니다.",
            overlap=None,
        )
        result = compile_policy(value, extracted)
        self.assertIsNone(result.payload)
        self.assertIn("overlap_evidence_missing", result.reasons)
        self.assertIn("payment_timing_evidence_missing", result.reasons)
        self.assertEqual(result.normalized["amount_won"], 1_000_000)

    def test_monthly_amount_alone_does_not_invent_a_payment_schedule(self) -> None:
        value, extracted, _ = case(amount="월 20만원을 12개월 동안 지원합니다.")
        result = compile_policy(value, extracted)
        self.assertIsNone(result.payload)
        self.assertIn("monthly_payment_anchor_missing", result.reasons)

    def test_overlapping_applications_and_named_pair_are_not_blanket_receipt_permission(self) -> None:
        for statement in ('다른 정책과 중복 신청 가능.',
                          '다른 정책과 중복 참여 가능.',
                          '가상 A정책과 중복 수혜 가능.'):
            with self.subTest(statement=statement):
                value, extracted, document = case(overlap=statement)
                if '가상 A' in statement:
                    extracted = (replace(extracted[0], spans=tuple(
                        _span(document, 'overlap', '중복 수혜 가능.') if s.kind == 'overlap' else s
                        for s in extracted[0].spans)),)
                result = compile_policy(value, extracted)
                self.assertIsNone(result.payload)
                self.assertIn('overlap_not_explicit', result.reasons)

    def test_full_source_lifetime_and_coded_conditions_cannot_be_dropped(self) -> None:
        for addition, reason in (('생애 1회 지원합니다.', 'source_lifetime_or_count_limit_unsupported'),
                                  ('[jobCd]\nfictional-code', 'source_coded_eligibility_requires_mapping')):
            with self.subTest(addition=addition):
                value, extracted, document = case()
                document = replace(document, text=document.text + '\n' + addition)
                value = replace(value, documents=(document,))
                extracted = (replace(extracted[0], document=document),)
                result = compile_policy(value, extracted)
                self.assertIsNone(result.payload)
                self.assertIn(reason, result.reasons)

    def test_full_source_cap_and_exception_survive_narrow_or_missing_locate(self) -> None:
        value, extracted, document = case(amount="월 최대 20만원을 12개월 동안 지원합니다.")
        narrow = replace(extracted[0], spans=tuple(
            _span(document, 'amount', '20만원') if span.kind == 'amount' else span
            for span in extracted[0].spans))
        result = compile_policy(value, (narrow,))
        self.assertIn('source_amount_not_single_fixed', result.reasons)
        value, extracted, _ = case(exception="단, 다른 지원사업 수혜자는 제외합니다.")
        dropped = replace(extracted[0], spans=tuple(s for s in extracted[0].spans if s.kind != 'exceptions'))
        result = compile_policy(value, (dropped,))
        self.assertIsNone(result.payload)
        self.assertIn('source_qualifier_requires_review', result.reasons)

    def test_exception_is_held_even_when_other_gates_pass(self) -> None:
        value, extracted, _ = case(exception="단, 다른 지원사업 수혜자는 제외합니다.")
        result = compile_policy(value, extracted)
        self.assertIsNone(result.payload)
        self.assertIn("exceptions_unsupported", result.reasons)

    def test_application_dates_must_exist_be_valid_and_agree(self) -> None:
        cases = (
            (
                "2026.01.01 ~ 2026.12.31",
                "2026.02.01 ~ 2026.11.30",
                "application_dates_contradictory",
            ),
            (
                "2026.02.30 ~ 2026.12.31",
                "2026.02.30 ~ 2026.12.31",
                "application_dates_invalid",
            ),
            ("상시 모집", "상시 모집", "application_deadline_unknown"),
        )
        for field_dates, span_dates, reason in cases:
            with self.subTest(field_dates=field_dates):
                value, extracted, document = case(
                    application=field_dates,
                )
                if reason == "application_dates_contradictory":
                    changed = replace(
                        document,
                        text=document.text + "\n\n변경 안내: " + span_dates,
                    )
                    value = replace(value, documents=(changed,))
                    extraction = replace(
                        extracted[0],
                        document=changed,
                        spans=tuple(
                            _span(
                                changed,
                                item.kind,
                                span_dates if item.kind == "application" else item.text,
                            )
                            for item in extracted[0].spans
                        ),
                    )
                else:
                    extraction = extracted[0]
                result = compile_policy(value, (extraction,))
                self.assertIsNone(result.payload)
                self.assertIn(reason, result.reasons)

    def test_unknown_or_unsupported_eligibility_holds_whole_record(self) -> None:
        value, extracted, _ = case(eligibility=("저소득 청년",))
        result = compile_policy(value, extracted)
        self.assertIsNone(result.payload)
        self.assertIn("eligibility_rule_unsupported", result.reasons)

    def test_business_and_missing_scope_are_held(self) -> None:
        for scope, reason in (
            ("business", "scope_business"),
            ("other", "scope_other"),
            ("uncertain", "scope_uncertain"),
            (None, "scope_missing"),
        ):
            with self.subTest(scope=scope):
                decisions = {"scope": scope} if scope is not None else {}
                value, extracted, _ = case(decisions=decisions)
                if scope is None:
                    extracted[0].decisions.pop("scope")
                result = compile_policy(value, extracted)
                self.assertIsNone(result.payload)
                self.assertIn(reason, result.reasons)

    def test_supported_age_selection_and_income_are_all_mapped(self) -> None:
        value, extracted, document = case(
            eligibility=(
                "만 19세 이상 34세 이하",
                "미취업자",
                "무주택",
                "연 소득 3천만 원 이하",
            )
        )
        result = compile_policy(value, extracted)
        payload = self.assert_catalog_payload(result, document)
        rules = {rule["condition_id"]: rule for rule in payload["conditions"]}
        self.assertEqual(rules["age"]["kind"], "age")
        self.assertEqual(rules["income_max"]["kind"], "income_max")
        self.assertIn(
            "job_seeker",
            {
                value
                for rule in rules.values()
                if rule["kind"] == "selection"
                for value in rule["values"]
            },
        )
        self.assertIn(
            "homeless",
            {
                value
                for rule in rules.values()
                if rule["kind"] == "selection"
                for value in rule["values"]
            },
        )

    def test_tampered_span_is_rejected_even_when_text_looks_identical(self) -> None:
        value, extracted, _ = case()
        amount = extracted[0].spans[0]
        tampered = replace(amount, end=amount.end + 1)
        result = compile_policy(
            value, (replace(extracted[0], spans=(tampered, *extracted[0].spans[1:])),)
        )
        self.assertIsNone(result.payload)
        self.assertIn("evidence_span_invalid", result.reasons)

    def test_multiple_sources_hold_but_normalized_numbers_survive(self) -> None:
        bindings = (
            AnalysisSource("youthcenter", EXTERNAL_ID, MATERIAL_SHA256),
            AnalysisSource("gov24", "supplement", "b" * 64),
        )
        value, extracted, _ = case(bindings=bindings)
        result = compile_policy(value, extracted)
        self.assertIsNone(result.payload)
        self.assertIn("multiple_sources_unsupported", result.reasons)
        self.assertEqual(result.normalized["amount_won"], 200_000)
        self.assertEqual(result.normalized["period_months"], 12)

    def test_multiple_documents_hold_but_first_extraction_is_retained(self) -> None:
        value, extracted, document = case()
        second = replace(document, id="youthcenter:1001:supplement")
        value = replace(value, documents=(document, second))
        second_extraction = replace(extracted[0], document=second, spans=())
        result = compile_policy(value, (extracted[0], second_extraction))
        self.assertIsNone(result.payload)
        self.assertIn("multiple_documents_unsupported", result.reasons)
        self.assertEqual(result.normalized["amount_won"], 200_000)

    def test_lump_sum_requires_an_explicit_payment_anchor(self) -> None:
        successful = (
            ("지원금 100만원을 선정 즉시 지급합니다.", 0),
            ("지원금 100만원을 지원 시작월부터 지급합니다.", 1),
        )
        for text, month in successful:
            with self.subTest(text=text):
                value, extracted, document = case(amount=text)
                result = compile_policy(value, extracted)
                payload = self.assert_catalog_payload(result, document)
                self.assertEqual(payload["payments"], [{"month": month, "amount_won": 1_000_000}])
                self.assertEqual(payload["benefit"]["benefit_amount"]["pay_timing"], "once")

        value, extracted, _ = case(amount="지원금 100만원을 일시 지급합니다.")
        result = compile_policy(value, extracted)
        self.assertIsNone(result.payload)
        self.assertTrue(any(reason.startswith("payment_timing_") for reason in result.reasons))


if __name__ == "__main__":
    unittest.main()
