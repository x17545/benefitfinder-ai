"""Virtual tests for structural F12 API and prose evidence ranges."""

from unittest import TestCase

from benefitfinder.ai.policy_evidence_context import (
    EvidenceFieldRange,
    api_field_ranges,
    evidence_context,
    field_kind,
    source_field_ranges,
)


class PolicyEvidenceContextTests(TestCase):
    def test_explicit_prose_sections_preserve_qualifiers_and_stop_at_unknown_heading(self):
        text = ('가상 서비스\r\n지원 대상\r\n거주 또는 거주예정 1인가구\r\n'
                '독립예정 가구 포함\r\n\r\n이용일시\r\n월·수 상담\r\n'
                '야간은 사전예약한 경우에 한함\r\n자치구별 시간이 다를 수 있음\r\n'
                '### 별도 안내\r\n가상 기타 정보\r\n신청 방법:\r\n전화 예약\r\n')
        fields = source_field_ranges(text)
        self.assertEqual([(f.name, f.start_line, f.end_line, f.kind) for f in fields], [
            ('지원대상', 2, 4, 'eligibility'), ('이용일시', 6, 9, 'service'),
            ('신청방법', 12, 13, 'application'),
        ])
        self.assertEqual(evidence_context(text, 'eligibility', 3, 3), (2, 4))
        self.assertEqual(evidence_context(text, 'timing', 7, 7), (6, 9))
        self.assertEqual(field_kind(text, 7, 9), 'service')
        self.assertIsNone(field_kind(text, 8, 11))

    def test_prose_labels_inside_an_api_field_do_not_split_the_api_context(self):
        text = (
            '[opaqueField]\n'
            '지원대상\n'
            '가상 구직자\n'
            '지원방법\n'
            '전화 예약\n'
            '지원대상: 가상 청년\n'
            '❍ 지원기간 2027. 3. ~ 9.\n'
            ' : 2027. 3. ~ 9. ❍ 지원기간 ※ 가상 예산 종료\n'
            '- : 구비서류 신청서\n'
            '초본 또는 재학증명서'
        )
        self.assertEqual(source_field_ranges(text), api_field_ranges(text))
        self.assertEqual(len(source_field_ranges(text)), 1)
        self.assertIsNone(field_kind(text, 3, 3))
        self.assertIsNone(field_kind(text, 9, 10))

    def test_explicit_inline_titles_keep_the_marker_line_and_following_body(self):
        text = (
            '머리말\n'
            '지원대상: 가상 청년\n'
            '거주 중이거나 거주 예정인 사람\n'
            '\n'
            '❍ 지원기간 2027. 3. ~ 9.\n'
            '※ 가상 예산 종료'
        )

        self.assertEqual(
            [
                (field.name, field.start_line, field.end_line, field.kind)
                for field in source_field_ranges(text)
            ],
            [
                ('지원대상', 2, 3, 'eligibility'),
                ('지원기간', 5, 6, 'program_period'),
            ],
        )
        self.assertEqual(field_kind(text, 2, 3), 'eligibility')
        self.assertEqual(field_kind(text, 5, 6), 'program_period')
        self.assertEqual(evidence_context(text, 'eligibility', 3, 3), (2, 3))
        self.assertEqual(evidence_context(text, 'program_period', 5, 5), (5, 6))
        # Restore the source section even when the requested role is wrong;
        # extraction corrects that role separately after context expansion.
        self.assertEqual(evidence_context(text, 'application', 3, 3), (2, 3))

    def test_explicit_section_markers_and_numbered_inline_titles_are_accepted(self):
        inline_lines = [
            *(f'{marker} 지원대상 가상 청년'
              for marker in ('❍', '○', '□', '■', '◆', '◇', '▶', '▷')),
            '1. 지원대상 가상 청년',
            '2) 지원대상 가상 청년',
        ]

        for line in inline_lines:
            with self.subTest(line=line):
                text = f'머리말\n{line}'
                self.assertEqual(
                    [
                        (field.name, field.start_line, field.end_line, field.kind)
                        for field in source_field_ranges(text)
                    ],
                    [('지원대상', 2, 2, 'eligibility')],
                )
                self.assertEqual(field_kind(text, 2, 2), 'eligibility')
                self.assertEqual(evidence_context(text, 'eligibility', 2, 2), (2, 2))

    def test_pdf_style_prefixes_require_a_structural_or_numeric_prefix(self):
        text = (
            ' : 2027. 3. ~ 9. ❍ 지원기간 ※ 가상 예산 종료\n'
            '- : 2027. 3. 2. ~ 9. 5. 신청기간 ※ 조기종료\n'
            ' : ❍ 지원대상 가상 구직자 또는 대학 재학생\n'
            '- : 구비서류 신청서\n'
            '초본 또는 재학증명서'
        )

        fields = source_field_ranges(text)
        self.assertEqual(
            [(field.name, field.start_line, field.end_line) for field in fields],
            [
                ('지원기간', 1, 1),
                ('신청기간', 2, 2),
                ('지원대상', 3, 3),
                ('구비서류', 4, 5),
            ],
        )
        self.assertEqual(field_kind(text, 1, 1), 'program_period')
        self.assertEqual(field_kind(text, 2, 2), 'application')
        self.assertEqual(field_kind(text, 3, 3), 'eligibility')
        self.assertEqual(evidence_context(text, 'program_period', 1, 1), (1, 1))
        self.assertEqual(evidence_context(text, 'application', 2, 2), (2, 2))
        self.assertEqual(evidence_context(text, 'eligibility', 3, 3), (3, 3))

        unclassified = (
            '가상 안내\n'
            '운영 안내 : 2027. 3. ~ 9. ❍ 지원기간 ※ 가상 예산 종료\n'
            '- 제출서류 미반환 시 다음 신청이 제한됩니다.'
        )
        self.assertEqual(source_field_ranges(unclassified), ())
        self.assertEqual(evidence_context(unclassified, 'program_period', 2, 2), (2, 2))

    def test_known_section_titles_map_to_their_evidence_roles(self):
        text = (
            '서비스 상세내용\n'
            '가상 상담과 취업 연계\n'
            '지원절차\n'
            '상담 후 신청\n'
            '이용절차\n'
            '온라인 또는 방문\n'
            '신청 및 접수\n'
            '가상 접수\n'
            '선정\n'
            '1) 서류 확인\n'
            '2) 선정: 요건 검토 및 선정(통보)\n'
            '지원기간\n'
            '2027. 3. ~ 9.'
        )

        self.assertEqual(
            [
                (field.name, field.start_line, field.end_line, field.kind)
                for field in source_field_ranges(text)
            ],
            [
                ('서비스상세내용', 1, 2, 'service'),
                ('지원절차', 3, 4, 'application'),
                ('이용절차', 5, 6, 'application'),
                ('신청및접수', 7, 8, 'application'),
                ('선정', 9, 11, 'application'),
                ('지원기간', 12, 13, 'program_period'),
            ],
        )
        self.assertEqual(field_kind(text, 1, 2), 'service')
        self.assertEqual(field_kind(text, 3, 4), 'application')
        self.assertEqual(field_kind(text, 5, 6), 'application')
        self.assertEqual(field_kind(text, 7, 8), 'application')
        self.assertEqual(field_kind(text, 9, 11), 'application')
        self.assertEqual(field_kind(text, 12, 13), 'program_period')
        self.assertEqual(evidence_context(text, 'service', 2, 2), (1, 2))
        self.assertEqual(evidence_context(text, 'application', 10, 10), (9, 11))

    def test_numbered_notice_and_page_marker_keep_the_entire_original_section(self):
        text = (
            '가상 안내\n'
            '4 유의사항\n'
            '- 가상 신청은 기한 전에 취소할 수 있습니다.\n'
            '추가 증빙서류가 필요할 수 있습니다.\n'
            '가상 중복 수혜 대상은 제외됩니다.\n'
            '--- 2 ---\n'
            '제출서류 미반환 시 다음 신청이 제한됩니다.\n'
            '□ 지원절차\n'
            '온라인 신청'
        )

        self.assertEqual(
            [
                (field.name, field.start_line, field.end_line, field.kind)
                for field in source_field_ranges(text)
            ],
            [
                ('유의사항', 2, 7, 'exceptions'),
                ('지원절차', 8, 9, 'application'),
            ],
        )
        self.assertEqual(
            tuple(text.splitlines()[1:7]),
            (
                '4 유의사항',
                '- 가상 신청은 기한 전에 취소할 수 있습니다.',
                '추가 증빙서류가 필요할 수 있습니다.',
                '가상 중복 수혜 대상은 제외됩니다.',
                '--- 2 ---',
                '제출서류 미반환 시 다음 신청이 제한됩니다.',
            ),
        )
        self.assertEqual(evidence_context(text, 'application', 9, 9), (8, 9))

    def test_distinct_titles_on_the_same_line_are_only_a_section_boundary(self):
        text = (
            '□ 지원대상\n'
            '가상 청년\n'
            '지원대상: 청년 신청방법: 방문\n'
            '□ 이용절차\n'
            '온라인 신청'
        )

        self.assertEqual(
            [
                (field.name, field.start_line, field.end_line, field.kind)
                for field in source_field_ranges(text)
            ],
            [
                ('지원대상', 1, 2, 'eligibility'),
                ('이용절차', 4, 5, 'application'),
            ],
        )
        self.assertIsNone(field_kind(text, 3, 3))
        self.assertEqual(evidence_context(text, 'eligibility', 3, 3), (3, 3))
        self.assertEqual(evidence_context(text, 'application', 3, 3), (3, 3))
        self.assertEqual(evidence_context(text, 'application', 5, 5), (4, 5))

    def test_body_mentions_and_dash_prefixed_sentences_do_not_start_new_sections(self):
        text = (
            '□ 지원대상\n'
            '가상 청년\n'
            '지원대상은 만 19세 이상의 청년입니다.\n'
            '- 제출서류 미반환 시 다음 신청이 제한됩니다.\n'
            '\n'
            '□ 신청 및 접수\n'
            '온라인 신청'
        )

        self.assertEqual(
            [
                (field.name, field.start_line, field.end_line, field.kind)
                for field in source_field_ranges(text)
            ],
            [
                ('지원대상', 1, 4, 'eligibility'),
                ('신청및접수', 6, 7, 'application'),
            ],
        )
        self.assertEqual(field_kind(text, 3, 4), 'eligibility')
        self.assertEqual(evidence_context(text, 'eligibility', 4, 4), (1, 4))

    def test_unlabeled_prose_remains_unclassified(self):
        self.assertEqual(source_field_ranges('가상 안내\n청년은 무료로 이용할 수 있습니다.'), ())

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
                EvidenceFieldRange("plcySprtCn", 2, 4, "support"),
                EvidenceFieldRange("opaqueField", 6, 7, "unknown"),
                EvidenceFieldRange("aplyYmd", 8, 9, "application"),
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
                EvidenceFieldRange("sprtTrgtMinAge", 1, 2, "eligibility"),
                EvidenceFieldRange("sprtTrgtMaxAge", 3, 4, "eligibility"),
                EvidenceFieldRange("MinAge", 5, 6, "unknown"),
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
        self.assertEqual(evidence_context(text, "service", 2, 2), (2, 7))
        unstructured = text.replace("신청 대상:", "가상 청년 대상은")
        self.assertEqual(evidence_context(unstructured, "eligibility", 2, 2), (2, 7))
        self.assertEqual(evidence_context(unstructured, "service", 2, 2), (2, 2))

    def test_threshold_is_not_mistaken_for_a_transaction_amount_formula(self) -> None:
        text = (
            "신청 대상: 만 19세 이상\n"
            "거래금액 = 5억원 이하\n"
            "가상 신청 안내"
        )

        self.assertEqual(evidence_context(text, "eligibility", 1, 1), (1, 3))
        unstructured = text.replace("신청 대상:", "가상 청년 대상은")
        self.assertEqual(evidence_context(unstructured, "eligibility", 1, 1), (1, 1))

    def test_legacy_formula_stops_before_an_inline_pdf_application_section(self):
        text = (
            '가상 청년은 주택 거래금액 기준을 충족해야 합니다.\n'
            '거래금액 = 임대보증금 + (월세 × 100)\n'
            '※ 위 산식은 가상 주택에만 적용합니다.\n'
            '- : 2028. 3. ~ 10. 신청기간 ※ 예외 신청은 별도 확인\n'
            '가상 접수 안내'
        )
        self.assertEqual(evidence_context(text, 'eligibility', 1, 1), (1, 3))
        self.assertEqual(field_kind(text, 4, 5), 'application')

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
                EvidenceFieldRange("plcySprtCn", 1, 1, "support"),
                EvidenceFieldRange("aplyYmd", 3, 4, "application"),
                EvidenceFieldRange("opaqueField", 6, 6, "unknown"),
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
