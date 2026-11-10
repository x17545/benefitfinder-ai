"""Virtual documents only: FAQ and footnote context grouping and closing."""

from __future__ import annotations

import dataclasses
from unittest import TestCase

from benefitfinder.ai.evidence_context import EvidenceGroup, close_context, context_groups


def _text(lines: list[str]) -> str:
    return chr(10).join(lines)


class EvidenceGroupShapeTests(TestCase):
    def test_frozen_dataclass_with_kind_start_end(self) -> None:
        group = EvidenceGroup(kind="faq", start=2, end=6)
        self.assertEqual(group.kind, "faq")
        self.assertEqual(group.start, 2)
        self.assertEqual(group.end, 6)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            group.start = 3  # type: ignore[misc]

    def test_context_groups_returns_tuple_of_groups(self) -> None:
        text = _text(["Q1 가상 질문입니다", "A 가상 답변입니다"])
        groups = context_groups(text)
        self.assertIsInstance(groups, tuple)
        for item in groups:
            self.assertIsInstance(item, EvidenceGroup)
            self.assertIn(item.kind, ("faq", "footnote"))
            self.assertLessEqual(1, item.start)
            self.assertLessEqual(item.start, item.end)
            self.assertLessEqual(item.end, len(text.splitlines()))


class FaqGroupingTests(TestCase):
    def test_answer_selection_attaches_wrapped_question_and_full_answer(self) -> None:
        text = _text(
            [
                "[가상] 자주 묻는 질문",
                "Q1 가상 취업 지원금은 누가 신청할 수 있나요",
                "  가상 질문 이어짐: 가상 만 19세부터 34세 청년",
                "A 가상 취업 준비 청년에게 월 10만원을 지급합니다",
                "  가상 답변 이어짐: 가상 신청 월부터 6개월간 지급합니다",
                "  - 가상 세부 목록: 가상 구직활동 확인서 제출",
                "Q2 가상 주거 지원금은 얼마인가요",
                "A 가상 월세 청년에게 월 15만원을 지급합니다",
                "지원내용",
                "[가상] 가상 지원 내용 본문이다",
            ]
        )
        groups = context_groups(text)
        expected = (
            EvidenceGroup(kind="faq", start=2, end=6),
            EvidenceGroup(kind="faq", start=7, end=8),
        )
        self.assertEqual(groups, expected)
        self.assertEqual(close_context(((4, 4),), groups), ((2, 6),))

    def test_question_only_attaches_answer(self) -> None:
        text = _text(
            [
                "[가상] 자주 묻는 질문",
                "Q1 가상 취업 지원금은 누가 신청할 수 있나요",
                "  가상 질문 이어짐: 가상 만 19세부터 34세 청년",
                "A 가상 취업 준비 청년에게 월 10만원을 지급합니다",
                "  가상 답변 이어짐: 가상 신청 월부터 6개월간 지급합니다",
                "Q2 가상 주거 지원금은 얼마인가요",
                "A 가상 월세 청년에게 월 15만원을 지급합니다",
            ]
        )
        groups = context_groups(text)
        expected = (
            EvidenceGroup(kind="faq", start=2, end=5),
            EvidenceGroup(kind="faq", start=6, end=7),
        )
        self.assertEqual(groups, expected)
        self.assertEqual(close_context(((2, 2),), groups), ((2, 5),))

    def test_toc_questions_without_answer_produce_no_faq(self) -> None:
        text = _text(
            [
                "목차",
                "Q1 가상 취업 지원금 질문 모음",
                "Q2 가상 주거 지원금 질문 모음",
                "지원내용",
                "[가상] 가상 지원 내용 본문이다",
            ]
        )
        groups = context_groups(text)
        self.assertEqual([g for g in groups if g.kind == "faq"], [])
        self.assertEqual(close_context(((2, 2),), groups), ((2, 2),))

    def test_adjacent_q1_q2_isolated(self) -> None:
        text = _text(
            [
                "[가상] 자주 묻는 질문",
                "Q1 가상 취업 질문입니다",
                "A 가상 취업 답변으로 월 10만원을 지급합니다",
                "Q2 가상 주거 질문입니다",
                "A 가상 주거 답변으로 월 15만원을 지급합니다",
                "지원내용",
            ]
        )
        groups = context_groups(text)
        expected = (
            EvidenceGroup(kind="faq", start=2, end=3),
            EvidenceGroup(kind="faq", start=4, end=5),
        )
        self.assertEqual(groups, expected)
        self.assertEqual(close_context(((3, 3),), groups), ((2, 3),))
        self.assertEqual(close_context(((4, 4),), groups), ((4, 5),))

    def test_cross_page_qa_single_group(self) -> None:
        text = _text(
            [
                "[가상] 자주 묻는 질문",
                "Q1 가상 취업 지원금은 언제 지급되나요",
                "=== PDF PAGE 1 ===",
                "1",
                "A 가상 매월 25일에 월 10만원을 지급합니다",
                "  가상 답변 이어짐: 가상 공휴일이면 전날 지급합니다",
                "Q2 가상 다음 질문입니다",
                "A 가상 다음 답변입니다",
            ]
        )
        groups = context_groups(text)
        expected = (
            EvidenceGroup(kind="faq", start=2, end=6),
            EvidenceGroup(kind="faq", start=7, end=8),
        )
        self.assertEqual(groups, expected)
        self.assertEqual(close_context(((5, 5),), groups), ((2, 6),))

    def test_faq_ends_before_section_headings(self) -> None:
        text = _text(
            [
                "Q1 가상 질문입니다",
                "A 가상 답변으로 월 10만원을 지급합니다",
                "지원대상",
                "Q2 가상 질문입니다",
                "A 가상 답변으로 월 15만원을 지급합니다",
                "신청방법",
                "[가상] 가상 신청 본문이다",
            ]
        )
        groups = context_groups(text)
        expected = (
            EvidenceGroup(kind="faq", start=1, end=2),
            EvidenceGroup(kind="faq", start=4, end=5),
        )
        self.assertEqual(groups, expected)
        self.assertEqual(close_context(((2, 2),), groups), ((1, 2),))

    def test_virtual_names_do_not_change_spans(self) -> None:
        base = [
            "Q1 {name} 질문입니다",
            "A {name} 답변으로 월 10만원을 지급합니다",
            "Q2 {name} 다음 질문입니다",
            "A {name} 다음 답변으로 월 15만원을 지급합니다",
        ]
        first = _text([line.format(name="가상 취업 지원금") for line in base])
        second = _text([line.format(name="가상 주거 지원금") for line in base])
        self.assertEqual(context_groups(first), context_groups(second))
        self.assertEqual(
            context_groups(first),
            (
                EvidenceGroup(kind="faq", start=1, end=2),
                EvidenceGroup(kind="faq", start=3, end=4),
            ),
        )


class FootnoteGroupingTests(TestCase):
    def test_parent_selected_includes_star_notes_and_wrapped_list(self) -> None:
        text = _text(
            [
                "- [가상] 가상 취업 지원금은 월 10만원이다",
                "  * 가상 각주: 가상 소득 기준 50퍼센트를 따른다",
                "  ** 가상 추가 각주: 가상 중복 수혜 제한을 확인한다",
                "  가상 이어짐: 가상 신청 월부터 적용한다",
                "  1) 가상 세부 목록: 가상 구직활동 확인서 제출",
                "- [가상] 가상 다음 본문: 가상 주거 지원금은 별도이다",
                "지원내용",
                "[가상] 가상 지원 내용 본문이다",
            ]
        )
        groups = context_groups(text)
        self.assertEqual(groups, (EvidenceGroup(kind="footnote", start=1, end=5),))
        self.assertEqual(close_context(((1, 1),), groups), ((1, 5),))

    def test_note_only_restores_parent(self) -> None:
        text = _text(
            [
                "- [가상] 가상 취업 지원금은 월 10만원이다",
                "  * 가상 각주: 가상 소득 기준 50퍼센트를 따른다",
                "  ** 가상 추가 각주: 가상 중복 수혜 제한을 확인한다",
                "  가상 이어짐: 가상 신청 월부터 적용한다",
                "- [가상] 가상 다음 본문이다",
            ]
        )
        groups = context_groups(text)
        self.assertEqual(groups, (EvidenceGroup(kind="footnote", start=1, end=4),))
        self.assertEqual(close_context(((2, 2),), groups), ((1, 4),))
        self.assertEqual(close_context(((3, 3),), groups), ((1, 4),))

    def test_plain_paragraph_parent_and_notes(self) -> None:
        text = _text(
            [
                "[가상] 가상 부모 본문이다",
                "* 가상 각주이다",
                "** 가상 추가 각주이다",
                "가상 이어짐: 가상 신청 월부터 적용한다",
                "",
                "[가상] 가상 다음 문단 본문이다",
            ]
        )
        groups = context_groups(text)
        self.assertEqual(groups, (EvidenceGroup(kind="footnote", start=1, end=4),))
        self.assertEqual(close_context(((2, 2),), groups), ((1, 4),))

    def test_stops_before_next_primary_bullet(self) -> None:
        text = _text(
            [
                "- [가상] 가상 부모 본문이다",
                "  * 가상 각주이다",
                "- [가상] 가상 다음 본문이다",
            ]
        )
        groups = context_groups(text)
        self.assertEqual(groups, (EvidenceGroup(kind="footnote", start=1, end=2),))
        self.assertEqual(close_context(((1, 1),), groups), ((1, 2),))

    def test_stops_before_maru_bullet(self) -> None:
        text = _text(
            [
                "- [가상] 가상 부모 본문이다",
                "  * 가상 각주이다",
                "※ [가상] 가상 다음 항목이다",
            ]
        )
        groups = context_groups(text)
        self.assertEqual(groups, (EvidenceGroup(kind="footnote", start=1, end=2),))

    def test_stops_before_section_heading(self) -> None:
        text = _text(
            [
                "- [가상] 가상 부모 본문이다",
                "  * 가상 각주이다",
                "지원내용",
                "[가상] 가상 본문이다",
            ]
        )
        groups = context_groups(text)
        self.assertEqual(groups, (EvidenceGroup(kind="footnote", start=1, end=2),))

    def test_cross_page_parent_notes(self) -> None:
        text = _text(
            [
                "- [가상] 가상 부모 본문이다",
                "  * 가상 각주 첫 줄이다",
                "=== PDF PAGE 2 ===",
                "  ** 가상 추가 각주가 다음 쪽에 이어진다",
                "  가상 이어짐: 가상 신청 월부터 적용한다",
                "- [가상] 가상 다음 본문이다",
            ]
        )
        groups = context_groups(text)
        self.assertEqual(groups, (EvidenceGroup(kind="footnote", start=1, end=5),))
        self.assertEqual(close_context(((4, 4),), groups), ((1, 5),))


class CloseContextTests(TestCase):
    def test_transitive_closure_across_chained_groups(self) -> None:
        groups = (
            EvidenceGroup(kind="faq", start=1, end=3),
            EvidenceGroup(kind="faq", start=3, end=5),
            EvidenceGroup(kind="faq", start=5, end=7),
        )
        self.assertEqual(close_context(((1, 1),), groups), ((1, 7),))

    def test_idempotence(self) -> None:
        text = _text(
            [
                "[가상] 자주 묻는 질문",
                "Q1 가상 질문입니다",
                "A 가상 답변으로 월 10만원을 지급합니다",
                "Q2 가상 다음 질문입니다",
                "A 가상 다음 답변으로 월 15만원을 지급합니다",
            ]
        )
        groups = context_groups(text)
        first = close_context(((3, 3),), groups)
        self.assertEqual(close_context(first, groups), first)

    def test_no_groups_no_changes(self) -> None:
        self.assertEqual(close_context(((2, 2), (5, 5)), ()), ((2, 2), (5, 5)))

    def test_merges_overlapping_and_adjacent_only(self) -> None:
        self.assertEqual(close_context(((1, 2), (3, 4)), ()), ((1, 4),))
        self.assertEqual(close_context(((1, 2), (4, 5)), ()), ((1, 2), (4, 5)))
        groups = (EvidenceGroup(kind="faq", start=10, end=12),)
        self.assertEqual(
            close_context(((1, 2), (3, 4), (11, 11)), groups),
            ((1, 4), (10, 12)),
        )

    def test_preserves_unrelated_and_never_grabs_next_faq(self) -> None:
        groups = (
            EvidenceGroup(kind="faq", start=2, end=6),
            EvidenceGroup(kind="faq", start=7, end=11),
        )
        self.assertEqual(close_context(((4, 4),), groups), ((2, 6),))
        self.assertEqual(close_context(((1, 1),), groups), ((1, 1),))
        self.assertEqual(
            close_context(((4, 4), (20, 20)), groups),
            ((2, 6), (20, 20)),
        )

    def test_never_grabs_unrelated_bullet_or_section(self) -> None:
        text = _text(
            [
                "- [가상] 가상 부모 본문이다",
                "  * 가상 각주이다",
                "- [가상] 가상 다음 본문이다",
                "지원내용",
                "[가상] 가상 본문이다",
            ]
        )
        groups = context_groups(text)
        self.assertEqual(close_context(((1, 1),), groups), ((1, 2),))
        self.assertEqual(close_context(((3, 3),), groups), ((3, 3),))
