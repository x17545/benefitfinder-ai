"""Virtual tests for structural F12 API field evidence ranges."""

from unittest import TestCase

from benefitfinder.ai.policy_evidence_context import (
    ApiFieldRange,
    api_field_ranges,
    evidence_context,
    field_kind,
)


class PolicyEvidenceContextTests(TestCase):
    def test_field_ranges_follow_only_complete_ascii_headers(self) -> None:
        text = (
            "머리말 [plcySprtCn]\n"
            "[plcySprtCn]\n"
            "가상 지원 A\n"
            "가상 지원 B\n"
            "   \n"
            "[opaqueField]\n"
            "가상 미확인 값\n"
            "[aplyYmd]\n"
            "가상 신청 기간\n"
            "\n"
        )

        self.assertEqual(
            api_field_ranges(text),
            (
                ApiFieldRange("plcySprtCn", 2, 4, "support"),
                ApiFieldRange("opaqueField", 6, 7, "unknown"),
                ApiFieldRange("aplyYmd", 8, 9, "application"),
            ),
        )
        self.assertEqual(text.splitlines(keepends=True)[1], "[plcySprtCn]\n")

    def test_field_kind_requires_one_known_field(self) -> None:
        text = (
            "[plcySprtCn]\n"
            "가상 지원\n"
            "[opaqueField]\n"
            "가상 미확인 값\n"
            "[plcyExplnCn]\n"
            "가상 혼합 설명\n"
            "[aplyYmd]\n"
            "가상 신청 기간"
        )

        self.assertEqual(field_kind(text, 1, 2), "support")
        self.assertEqual(field_kind(text, 5, 6), "mixed")
        self.assertEqual(field_kind(text, 7, 7), "application")
        self.assertIsNone(field_kind(text, 2, 3))
        self.assertIsNone(field_kind(text, 3, 4))
        self.assertIsNone(field_kind(text, 6, 7))
        self.assertIsNone(field_kind(text, 0, 2))
        self.assertIsNone(field_kind(text, 8, 9))

    def test_age_fields_use_full_api_names(self) -> None:
        text = (
            "[sprtTrgtMinAge]\n"
            "19\n"
            "[sprtTrgtMaxAge]\n"
            "39\n"
            "[MinAge]\n"
            "가상 약칭"
        )

        self.assertEqual(
            api_field_ranges(text),
            (
                ApiFieldRange("sprtTrgtMinAge", 1, 2, "eligibility"),
                ApiFieldRange("sprtTrgtMaxAge", 3, 4, "eligibility"),
                ApiFieldRange("MinAge", 5, 6, "unknown"),
            ),
        )
        self.assertEqual(field_kind(text, 1, 2), "eligibility")
        self.assertEqual(field_kind(text, 3, 4), "eligibility")
        self.assertIsNone(field_kind(text, 5, 6))

    def test_evidence_context_expands_whole_field_without_crossing_next_header(self) -> None:
        text = (
            "가상 공고\n"
            "[plcySprtCn]\n"
            "지원 A 또는 B\n"
            "\n"
            "[aplyYmd]\n"
            "신청: 11월\n"
            "마감: 12월\n"
            "\n"
            "[detailSupport]\n"
            "가상 상세 지원\n"
            "\n"
            "[bizPrdBgngYmd]\n"
            "가상 사업 시작일\n"
            "[opaqueField]\n"
            "가상 미확인 값"
        )

        self.assertEqual(evidence_context(text, "amount", 3, 3), (2, 3))
        self.assertEqual(evidence_context(text, "application", 6, 6), (5, 7))
        self.assertEqual(evidence_context(text, "service", 10, 10), (9, 10))
        self.assertEqual(evidence_context(text, "program_period", 13, 13), (12, 13))
        self.assertEqual(evidence_context(text, "source_context", 15, 15), (14, 15))

    def test_eligibility_keeps_an_explicit_transaction_formula_and_conditions(self) -> None:
        text = (
            "가상 공고\n"
            "신청 대상: 만 19세 이상\n"
            "거래금액 = (주택가격 - 임대보증금)\n"
            "※ 위 산식에서 주택가격은 가상 매매가격입니다.\n"
            "- 전세: 임대보증금 + (월세 × 100)\n"
            "- 매매: 매매가격\n"
            "※ 상가와 오피스텔은 제외합니다.\n"
            "□ 신청 방법\n"
            "온라인 신청"
        )

        self.assertEqual(evidence_context(text, "eligibility", 2, 2), (2, 7))
        self.assertEqual(evidence_context(text, "service", 2, 2), (2, 2))

    def test_threshold_is_not_mistaken_for_a_transaction_amount_formula(self) -> None:
        text = (
            "신청 대상: 만 19세 이상\n"
            "거래금액 = 5억원 이하\n"
            "가상 신청 안내"
        )

        self.assertEqual(evidence_context(text, "eligibility", 1, 1), (1, 1))

    def test_crlf_empty_and_unknown_fields_keep_exact_line_counts(self) -> None:
        text = (
            "[plcySprtCn]\r\n"
            "\r\n"
            "[aplyYmd]\r\n"
            "가상 신청\r\n"
            "\r\n"
            "[opaqueField]\r\n"
            "\r\n"
        )

        self.assertEqual(
            api_field_ranges(text),
            (
                ApiFieldRange("plcySprtCn", 1, 1, "support"),
                ApiFieldRange("aplyYmd", 3, 4, "application"),
                ApiFieldRange("opaqueField", 6, 6, "unknown"),
            ),
        )
        self.assertEqual(field_kind(text, 4, 4), "application")
        self.assertEqual(evidence_context(text, "application", 4, 4), (3, 4))
        self.assertEqual(evidence_context(text, "service", 7, 7), (7, 7))
        self.assertTrue(text.endswith("\r\n"))

    def test_large_field_is_returned_without_truncation(self) -> None:
        support_lines = [f"가상 지원 항목 {number}" for number in range(1, 241)]
        lines = ["[plcySprtCn]", *support_lines, "[aplyYmd]", "가상 신청"]
        text = "\n".join(lines)

        self.assertEqual(
            evidence_context(text, "amount", 240, 240),
            (1, 241),
        )

    def test_invalid_or_cross_field_ranges_are_returned_unchanged(self) -> None:
        text = (
            "[plcySprtCn]\n"
            "가상 지원\n"
            "[aplyYmd]\n"
            "가상 신청"
        )

        self.assertEqual(evidence_context(text, "amount", 0, 2), (0, 2))
        self.assertEqual(evidence_context(text, "amount", 5, 5), (5, 5))
        self.assertEqual(evidence_context(text, "amount", 3, 2), (3, 2))
        self.assertEqual(evidence_context(text, "amount", 2, 3), (2, 3))
