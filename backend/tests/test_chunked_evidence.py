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


def empty() -> dict[str, list[list[int]]]:
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
        def chat(prompt: list[dict[str, str]]) -> str:
            messages.append(prompt)
            data = empty()
            if len(messages) == 1:
                data.update(eligibility=[[5, 6], [8, 8]], exceptions=[[8, 8]],
                            amount=[[9, 9]], timing=[[9, 9]])
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

    def test_invalid_or_invented_range_never_produces_complete_result(self) -> None:
        for data in ({**empty(), 'amount': [[]]},
                     {**empty(), 'eligibility': [[True, 2]]},
                     {**empty(), 'eligibility': [[1, 500]]},
                     {**empty(), 'eligibility': [[3, 2]]},
                     {**empty(), 'unexpected': []}):
            with self.subTest(data=data), self.assertRaisesRegex(ValueError, '^chunk_response_invalid$'):
                extract_chunked_evidence(batch(), chat=lambda _: json.dumps(data))
        with self.assertRaisesRegex(ValueError, '^chunk_response_invalid$'):
            extract_chunked_evidence(batch(), chat=lambda _: '{"eligibility":[],"eligibility":[]}')

    def test_failure_on_later_chunk_is_not_partial_success(self) -> None:
        count = 0
        def chat(_: list[dict[str, str]]) -> str:
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
                model.return_value.side_effect = lambda _: json.dumps(empty())
                with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                    self.assertEqual(main(args), 0)
                self.assertEqual(model.return_value.call_count, 3)
                snapshot = load_rule_snapshot(root/'out/rules-pending.json')
                self.assertEqual(snapshot.batch.documents[0].text, TEXT)
                self.assertFalse(snapshot.reviews)
                self.assertTrue((root/'out/completion.json').exists())
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
