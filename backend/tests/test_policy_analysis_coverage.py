"""Actionable multi-document reads keep existing coverage and error gates intact."""

import json
import unittest

from backend.tests.test_policy_analysis_agent import (
    ScriptedChat, call, extract, fetch, make_input, reply, tool_payloads,
)
from benefitfinder.ai.policy_analysis_agent import analyze_policy


class PolicyAnalysisCoverageTest(unittest.TestCase):
    def test_separate_application_section_does_not_expand_cautions_into_a_long_directory(self):
        caution = '유의사항\n예약 없이 방문하면 대기할 수 있습니다.\n최소 이틀 전에 예약해 주세요.'
        directory = '\n'.join(f'가상 지역 {number}: 가상 접수기관 안내' for number in range(70))
        text = caution + '\n\n신청하기\n' + directory
        lines = len(text.splitlines())
        result = analyze_policy(make_input((text,)), chat=ScriptedChat([
            reply(fetch('doc-1', 1, lines)), reply(call('check_missing')),
            reply(call('save_candidate')),
        ]))
        exceptions = [item.evidence.quote for item in result.additional if item.kind == 'exceptions']
        self.assertEqual(exceptions, [caution])
        self.assertEqual(result.facts[0].document.text, text)
        self.assertEqual(result.trace[-1].outcome, 'needs_review')

    def test_read_hints_cover_each_source_and_bound_line_count_and_character_size(self):
        texts = ('가상 첫 문서\n끝', '\n'.join('가' * 200 for _ in range(65)),
                 '\n'.join('나' for _ in range(165)))
        seen = set()
        calls = []

        def chat(messages, tools):
            state = json.loads(messages[-1]['content'])
            pending = state['pending_reads']
            if pending:
                window = pending[0]
                doc = window['doc_id']
                start, end = window['start_line'], window['end_line']
                self.assertGreaterEqual(start, 1)
                self.assertLessEqual(end - start + 1, 160)
                lines = texts[int(doc.removeprefix('doc-')) - 1].splitlines()
                self.assertLessEqual(sum(len(line) for line in lines[start - 1:end]), 6000)
                self.assertNotIn((doc, start, end), seen)
                seen.add((doc, start, end))
                self.assertNotIn('가상 첫 문서', messages[-1]['content'])
                calls.append(window)
                return reply(fetch(doc, start, end))
            if state['check_required_before_save']:
                return reply(call('check_missing'))
            return reply(call('save_candidate'))

        result = analyze_policy(make_input(texts), chat=chat)
        self.assertEqual({item['doc_id'] for item in calls}, {'doc-1', 'doc-2', 'doc-3'})
        self.assertEqual(len(calls), 6)
        self.assertEqual(result.trace[-1].outcome, 'needs_review')
        self.assertTrue(result.missing)

    def test_premature_save_points_to_unread_source_without_reading_or_saving_it(self):
        scripted = ScriptedChat([
            reply(fetch('doc-1', 1, 1)), reply(call('check_missing')),
            reply(call('save_candidate')), reply(fetch('doc-2', 1, 1)),
            reply(call('check_missing')), reply(call('save_candidate')),
        ])

        def chat(messages, tools):
            if len(scripted.messages) == 3:
                feedback = tool_payloads(messages)[-1]
                self.assertEqual(feedback['error'], 'coverage_incomplete')
                self.assertEqual(feedback['next_tool'], 'fetch_document')
                self.assertEqual(feedback['next_tool_arguments'],
                                 {'doc_id': 'doc-2', 'start_line': 1, 'end_line': 1})
                self.assertEqual(json.loads(messages[-1]['content'])['pending_reads'],
                                 [feedback['next_tool_arguments']])
                self.assertNotIn('두번째 비공개가 아닌 가상 본문', json.dumps(feedback, ensure_ascii=False))
            return scripted(messages, tools)

        result = analyze_policy(make_input(('가상 첫 문서', '두번째 비공개가 아닌 가상 본문')), chat=chat)
        self.assertEqual(result.trace[2].outcome, 'invalid')
        self.assertEqual(result.trace[-1].outcome, 'needs_review')

    def test_invalid_window_has_exact_recovery_range_and_duplicate_keeps_other_source_pending(self):
        scripted = ScriptedChat([
            reply(fetch('doc-1', 1, 900)), reply(fetch('doc-1', 1, 1)),
            reply(extract('doc-1', 'eligibility', 1, 1)),
            reply(extract('doc-1', 'eligibility', 1, 1)),
            reply(fetch('doc-2', 1, 1)), reply(call('check_missing')),
            reply(call('save_candidate')),
        ])

        def chat(messages, tools):
            if len(scripted.messages) == 1:
                feedback = tool_payloads(messages)[-1]
                self.assertEqual(feedback['error'], 'line_range_invalid')
                self.assertEqual(feedback['next_tool_arguments'],
                                 {'doc_id': 'doc-1', 'start_line': 1, 'end_line': 1})
            if len(scripted.messages) == 4:
                self.assertTrue(tool_payloads(messages)[-1]['duplicate'])
                state = json.loads(messages[-1]['content'])
                self.assertEqual(state['pending_reads'],
                                 [{'doc_id': 'doc-2', 'start_line': 1, 'end_line': 1}])
            return scripted(messages, tools)

        result = analyze_policy(make_input(('만 20세 이상 가상 청년', '가상 보충 원문')), chat=chat)
        self.assertEqual(len(result.facts[0].eligibility), 1)
        self.assertEqual(result.trace[0].outcome, 'invalid')
        self.assertEqual(result.trace[3].outcome, 'duplicate')


if __name__ == '__main__':
    unittest.main()
