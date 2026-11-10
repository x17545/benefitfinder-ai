"""Virtual documents only: context boundaries, failed extraction and pending artifacts."""

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from benefitfinder.ai.chunked_evidence import extract_chunked_evidence
from benefitfinder.application.policy_rules import PolicyReference, RuleReviewBatch, SourceDocument
from benefitfinder.application.source_chunks import plan_source_chunks
from benefitfinder.infrastructure.policy_rule_files import load_rule_snapshot, write_rule_batch

TEXT = ("=== PDF PAGE 1 ===\n[가상] 청년 임차 지원\n신청 기준 안내\n"
        "=== PDF PAGE 2 ===\n[가상] 독립 가구 소득은 50% 이하\n다만 다음 쪽의 예외를 함께 확인\n"
        "=== PDF PAGE 3 ===\n[가상] 혼인한 경우 부모 소득 조사 제외\n매월 25일 10만원 지급\n"
        "=== PDF PAGE 4 ===\n[가상] 증빙 제출 안내\n추가 안내\n"
        "=== PDF PAGE 5 ===\n[가상] 담당 부서\n문의 방법\n")


def batch(text: str = TEXT) -> RuleReviewBatch:
    document = SourceDocument('virtual', 'virtual-policy', 'https://example.invalid/virtual',
                              'virtual-v1', datetime(2026, 1, 1, tzinfo=timezone.utc), text, True)
    return RuleReviewBatch((document,), (PolicyReference('virtual-policy', 'youth', ('housing',)),), ())


def empty() -> dict[str, list[int]]:
    return {k: [] for k in ('eligibility', 'amount', 'timing', 'exceptions')}


class SourceChunkTests(TestCase):
    def test_cores_cover_every_line_and_whole_neighbors_overlap(self) -> None:
        chunks = plan_source_chunks(TEXT)
        self.assertEqual([(c.core_start, c.core_end) for c in chunks], [(1, 6), (7, 12), (13, 15)])
        self.assertEqual([(c.start, c.end) for c in chunks], [(1, 9), (4, 15), (10, 15)])
        self.assertEqual([i for c in chunks for i in range(c.core_start, c.core_end + 1)],
                         list(range(1, 16)))
        # The condition ending on page 2 and exception on page 3 are visible together.
        self.assertTrue(any(c.start <= 5 and c.end >= 8 for c in chunks))

    def test_shrink_core_without_dropping_halo(self) -> None:
        chunks = plan_source_chunks(TEXT, max_lines=9)
        self.assertTrue(all(c.end - c.start + 1 <= 9 for c in chunks))
        self.assertEqual(chunks[1].core_start, 7)
        self.assertEqual(chunks[1].start, 4)
        self.assertEqual(chunks[1].end, 12)
        self.assertEqual(chunks[-1].core_end, 15)

    def test_oversized_context_fails_without_truncation(self) -> None:
        with self.assertRaisesRegex(ValueError, '^chunk_context_limit$'):
            plan_source_chunks(TEXT, max_lines=5)
        with self.assertRaisesRegex(ValueError, '^chunk_context_limit$'):
            plan_source_chunks('a' * 6001)
        with self.assertRaisesRegex(ValueError, '^chunk_input_invalid$'):
            plan_source_chunks(TEXT, max_lines=True)

    def test_plain_text_whole_paragraphs_and_line_endings(self) -> None:
        text = 'a\r\nb\r\n\r\nc\r\nd\r\n\r\ne\r\nf\r\n\r\ng'
        chunks = plan_source_chunks(text)
        self.assertEqual(chunks[0].core_end, 6)
        self.assertEqual(chunks[0].end, 9)
        self.assertEqual(chunks[-1].core_end, 10)


class ChunkedEvidenceTests(TestCase):
    def test_full_source_ranges_separate_exceptions_and_pending_output(self) -> None:
        original = batch()
        messages = []
        progress = []
        def chat(prompt: list[dict[str, str]], visible: tuple[int, ...]) -> str:
            messages.append(prompt)
            data = empty()
            if len(messages) == 1:
                data.update(eligibility=[5, 6, 8], exceptions=[8],
                            amount=[9], timing=[9])
            return json.dumps(data)
        facts, trace = extract_chunked_evidence(original, chat=chat,
                                                progress=lambda d, n: progress.append((d, n)))
        self.assertEqual(len(messages), 3)
        self.assertIn('8: [가상] 혼인한 경우 부모 소득 조사 제외', messages[0][1]['content'])
        self.assertEqual(progress, [(1, 3), (2, 3), (3, 3)])
        self.assertEqual(facts['source']['content_sha256'], original.documents[0].content_sha256)
        self.assertEqual(facts['status'], 'needs_review')
        self.assertEqual(facts['benefit_amount']['items'][0]['amounts_won'], [100000])
        self.assertIsNone(facts['payment_timing']['scheduled_monthly_day'])
        self.assertEqual(len(facts['eligibility']['items']), 2)
        self.assertEqual(trace['exceptions'][0]['evidence']['location'], 'text lines 8-8')
        self.assertTrue(trace['extraction_complete'])
        self.assertEqual(trace['approval_effect'], 'none')
        self.assertEqual(original, batch())
        for item in facts['eligibility']['items']:
            self.assertIn(item['evidence']['quote'], TEXT)

    def test_structural_context_is_distinct_from_model_and_does_not_add_amount_values(self) -> None:
        text = ('Q1 [가상] 소득 100만원과 지원은?\n'
                'A [가상] 지원금 10만원 지급\n'
                '* [가상] 해당 지급의 예외\n'
                '추가 조건을 함께 확인\n'
                'Q2 [가상] 다른 질문\nA 별도 답변\n')
        choices = {**empty(), 'eligibility': [2], 'amount': [2], 'exceptions': [3]}
        facts, trace = extract_chunked_evidence(batch(text), chat=lambda *args: json.dumps(choices))
        self.assertEqual(facts['eligibility']['items'][0]['evidence']['location'], 'text lines 1-4')
        self.assertIn('추가 조건', trace['exceptions'][0]['evidence']['quote'])
        self.assertNotIn('Q2', trace['exceptions'][0]['evidence']['quote'])
        self.assertEqual(facts['benefit_amount']['items'][0]['amounts_won'], [100000])
        self.assertEqual(trace['model_only_facts']['eligibility']['items'][0]['evidence']['location'],
                         'text lines 2-2')
        self.assertEqual(trace['chunks'][0]['selected']['eligibility'], ((2, 2),))
        self.assertEqual(trace['chunks'][0]['context_added']['eligibility'], ((1, 1), (3, 4)))
        self.assertEqual(trace['approval_effect'], 'none')

    def test_unsplittable_faq_fails_before_model(self) -> None:
        pages = ['=== PDF PAGE 1 ===\nQ1 가상 질문\nA 가상 답변\n' + '가상 본문\n' * 60]
        for i in range(2, 5):
            pages.append(f'=== PDF PAGE {i} ===\nQ&A\n' + '가상 계속 본문\n' * 60)
        pages.append('=== PDF PAGE 5 ===\nQ2 다음 질문\nA 다음 답변\n')
        with patch('builtins.print') as chat, self.assertRaisesRegex(ValueError, '^chunk_context_limit$'):
            extract_chunked_evidence(batch(''.join(pages)), chat=chat)
        chat.assert_not_called()

    def test_invalid_or_invented_range_never_produces_complete_result(self) -> None:
        for data in ({**empty(), 'amount': [[]]},
                     {**empty(), 'eligibility': [True, 2]},
                     {**empty(), 'eligibility': [1, 500]},
                     {**empty(), 'eligibility': [[3, 2]]},
                     {**empty(), 'unexpected': []}):
            with self.subTest(data=data), self.assertRaisesRegex(ValueError, '^chunk_response_invalid$'):
                extract_chunked_evidence(batch(), chat=lambda *args: json.dumps(data))
        with self.assertRaisesRegex(ValueError, '^chunk_response_invalid$'):
            extract_chunked_evidence(batch(), chat=lambda *args: '{"eligibility":[],"eligibility":[]}')

    def test_invalid_range_has_one_model_repair_without_swapping_or_dropping(self) -> None:
        prompts = []
        def chat(messages: list[dict[str, str]], visible: tuple[int, ...]) -> str:
            prompts.append(list(messages))
            result = empty()
            if len(prompts) == 1:
                result['eligibility'] = [[6, 5]]
            elif len(prompts) == 2:
                result['eligibility'] = [5, 6]
            return json.dumps(result)
        facts, trace = extract_chunked_evidence(batch(), chat=chat)
        self.assertEqual(len(prompts), 4)
        self.assertEqual(prompts[1][-2]['role'], 'assistant')
        self.assertIn('원문 줄 번호', prompts[1][-1]['content'])
        self.assertEqual(trace['chunks'][0]['attempts'], 2)
        self.assertEqual(trace['chunks'][0]['repair_reason'], 'chunk_response_invalid')
        self.assertEqual(facts['eligibility']['items'][0]['evidence']['location'], 'text lines 5-6')
        calls = []
        def always_invalid(messages: list[dict[str, str]], visible: tuple[int, ...]) -> str:
            calls.append(messages)
            return json.dumps({**empty(), 'eligibility': [[500, 1]]})
        with self.assertRaisesRegex(ValueError, '^chunk_response_invalid$'):
            extract_chunked_evidence(batch(), chat=always_invalid)
        self.assertEqual(len(calls), 2)

    def test_blank_only_selection_rejected_before_checkpoint_and_overlap_union(self) -> None:
        original = batch(TEXT.replace('신청 기준 안내', ''))
        calls = []
        checkpoints = []
        def chat(messages: list[dict[str, str]], visible: tuple[int, ...]) -> str:
            calls.append(visible)
            self.assertNotIn(3, visible)
            return json.dumps({**empty(), 'eligibility': [3]})
        with self.assertRaisesRegex(ValueError, '^chunk_response_invalid$'):
            extract_chunked_evidence(original, chat=chat, checkpoint=checkpoints.append)
        self.assertEqual(len(calls), 2)
        self.assertFalse(checkpoints)
        from benefitfinder.ai.chunked_evidence import _coalesce
        self.assertEqual(_coalesce({(5, 6), (6, 8)}, TEXT.splitlines(keepends=True)), [(5, 8)])
        self.assertEqual(_coalesce({(5, 5), (8, 8)}, TEXT.splitlines(keepends=True)), [(5, 5), (8, 8)])
        self.assertEqual(_coalesce({(1, 1), (3, 3)}, ['a\n', '\n', 'b']), [(1, 3)])

    def test_failure_on_later_chunk_is_not_partial_success(self) -> None:
        count = 0
        def chat(_: list[dict[str, str]], visible: tuple[int, ...]) -> str:
            nonlocal count
            count += 1
            if count == 2:
                raise ValueError('chunk_model_failed')
            return json.dumps(empty())
        with self.assertRaisesRegex(ValueError, '^chunk_model_failed$'):
            extract_chunked_evidence(batch(), chat=chat)
        self.assertEqual(count, 2)

    def test_oversized_source_is_validated_before_model(self) -> None:
        with patch('builtins.print') as chat, self.assertRaisesRegex(ValueError, '^chunk_context_limit$'):
            extract_chunked_evidence(batch('가' * 6001), chat=chat)
        chat.assert_not_called()

    def test_create_only_complete_run_no_approvals_and_failure_no_completion(self) -> None:
        from benefitfinder.jobs.__main__ import main
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root/'input.json'
            write_rule_batch(source, batch())
            args = ['extract-chunked-facts', '--input', str(source), '--output-dir', str(root/'out')]
            with patch('benefitfinder.jobs.__main__.LocalE4BChat') as model:
                model.return_value.side_effect = lambda *args: json.dumps(empty())
                with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                    self.assertEqual(main(args), 0)
                self.assertEqual(model.return_value.call_count, 3)
                snapshot = load_rule_snapshot(root/'out/rules-pending.json')
                self.assertEqual(snapshot.batch.documents[0].text, TEXT)
                self.assertFalse(snapshot.reviews)
                self.assertTrue((root/'out/completion.json').exists())
                self.assertEqual(len(list((root/'out/chunks').glob('*.json'))), 3)
                before = {p: p.read_bytes() for p in root.rglob('*.json')}
                model.reset_mock()
                with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                    self.assertEqual(main(args), 1)
                model.assert_not_called()
                self.assertTrue(all(p.read_bytes() == data for p, data in before.items()))
            args[-1] = str(root/'failed')
            with patch('benefitfinder.jobs.__main__.LocalE4BChat') as model:
                model.return_value.side_effect = ValueError('PRIVATE RESPONSE')
                stderr = StringIO()
                with redirect_stdout(StringIO()), redirect_stderr(stderr):
                    self.assertEqual(main(args), 1)
                self.assertNotIn('PRIVATE', stderr.getvalue())
                self.assertFalse((root/'failed/completion.json').exists())
                self.assertFalse((root/'failed/facts-pending.json').exists())
