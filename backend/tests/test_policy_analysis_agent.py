"""Virtual multi-document F12 Agent behavior through the public analyzer."""

from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from io import StringIO
import json
from contextlib import redirect_stderr, redirect_stdout
from time import sleep
from unittest import TestCase

from benefitfinder.ai.policy_analysis_agent import analyze_policy
from benefitfinder.application.policy_analysis import (
    AnalysisPolicy,
    AnalysisSource,
    AnalysisStep,
    PolicyAnalysisInput,
)
from benefitfinder.application.policy_rules import (
    RuleDirection,
    RuleKind,
    SourceDocument,
)

SOURCE_ID = "youth:f12:source"
TARGET_ID = "youth:f12:target"
OTHER_ID = "youth:f12:other"
NOW = datetime(2026, 11, 12, tzinfo=timezone.utc)

DOC_ONE = """가상 하늘 지원 안내
만 19세 이상 청년에게 신청할 수 있습니다.
월 10만원을 지급합니다.
신청 기간은 상시 접수입니다.
가상 달빛 수당과 동시에 받을 수 없습니다.
단, 단기 과정 참여자는 예외로 인정합니다.
매월 지급합니다."""

DOC_TWO = """가상 하늘 주거 안내
만 19세 이상 청년 대상입니다.
신청 기간은 12월 31일까지입니다.
매월 말에 지급합니다.
면접 정장 대여 3회를 제공합니다."""

DOC_THREE = "등록된 추가 안내입니다."


def call(tool_name: str, **arguments: object) -> dict[str, object]:
    return {"function": {"name": tool_name, "arguments": arguments}}


def reply(*calls: dict[str, object]) -> dict[str, object]:
    return {"role": "assistant", "content": "", "tool_calls": list(calls)}


def extract(
    doc_id: str,
    kind: str,
    start: int,
    end: int,
    **details: object,
) -> dict[str, object]:
    return call(
        "extract_rule",
        doc_id=doc_id,
        kind=kind,
        start_line=start,
        end_line=end,
        **details,
    )


def fetch(doc_id: str, start: int, end: int) -> dict[str, object]:
    return call("fetch_document", doc_id=doc_id, start_line=start, end_line=end)


def make_input(
    texts: tuple[str, ...] = (DOC_ONE, DOC_TWO, DOC_THREE),
    policies: tuple[AnalysisPolicy, ...] | None = None,
) -> PolicyAnalysisInput:
    documents = tuple(
        SourceDocument(
            f"doc-{index}",
            SOURCE_ID,
            f"https://example.invalid/f12-{index}",
            f"virtual-v{index}",
            NOW,
            text,
            True,
        )
        for index, text in enumerate(texts, 1)
    )
    return PolicyAnalysisInput(
        policy_id=SOURCE_ID,
        documents=documents,
        policies=policies
        or (
            AnalysisPolicy(
                SOURCE_ID,
                "가상 하늘 통합 지원",
                ("employment", "housing"),
                ("하늘 지원",),
            ),
            AnalysisPolicy(TARGET_ID, "가상 달빛 수당", ("employment",), ("달빛",)),
        ),
        collection_sources=(
            AnalysisSource("youthcenter", "virtual-f12-source", "a" * 64),
        ),
    )


class ScriptedChat:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = list(responses)
        self.messages: list[list[dict[str, object]]] = []
        self.tools: list[list[dict[str, object]]] = []

    def __call__(
        self,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
    ) -> dict[str, object]:
        self.messages.append(deepcopy(messages))
        self.tools.append(deepcopy(tools))
        if not self.responses:
            raise AssertionError("script exhausted")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def tool_payloads(messages: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        json.loads(message["content"])
        for message in messages
        if message.get("role") == "tool" and isinstance(message.get("content"), str)
    ]


class PolicyAnalysisAgentTests(TestCase):
    def test_read_prose_sections_survive_model_omission_and_preserve_service_qualifiers(self):
        api = '[detailTarget]\n만 23세 이상 청년'
        prose = ('지원대상\r\n거주 또는 거주예정 1인가구(독립가구 예정 포함)\r\n'
                 '이용료\r\n0원(증빙서류 제출 불필요)\r\n'
                 '이용일시\r\n월·목 상담, 야간 동행은 사전예약에 한함\r\n'
                 '운영시간은 자치구별로 다를 수 있음\r\n'
                 '지원방법\r\n예약 후 전화 또는 대면상담\r\n'
                 '유의사항\r\n최소 3일 전 신청\r\n특정 매물을 추천하는 서비스는 아님')
        scripted = ScriptedChat([
            reply(fetch('doc-1', 1, 2)), reply(call('check_missing')),
            reply(fetch('doc-2', 1, 12)), reply(call('check_missing')),
            reply(extract('doc-2', 'amount', 4, 4), extract('doc-2', 'timing', 6, 6)),
            reply(call('check_missing')), reply(call('save_candidate')),
        ])
        result = analyze_policy(make_input((api, prose)), chat=scripted)
        self.assertEqual(len(result.facts[1].eligibility), 1)
        target = result.facts[1].eligibility[0]
        self.assertIn('거주 또는 거주예정', target.quote)
        self.assertIn('독립가구 예정 포함', target.quote)
        self.assertIn('\r\n', target.quote)
        self.assertEqual(target.location, 'lines 1-2')
        self.assertFalse(result.facts[1].amounts)
        self.assertFalse(result.facts[1].payment_timing)
        by_kind = {}
        for item in result.additional:
            if item.evidence.document_id == 'doc-2':
                by_kind.setdefault(item.kind, []).append(item.evidence.quote)
        self.assertTrue(any('증빙서류 제출 불필요' in q for q in by_kind['service']))
        self.assertTrue(any('사전예약에 한함' in q and '자치구별' in q for q in by_kind['service']))
        self.assertIn('예약 후 전화 또는 대면상담', '\n'.join(by_kind['application']))
        self.assertIn('최소 3일 전 신청', '\n'.join(by_kind['exceptions']))
        self.assertIn('추천하는 서비스는 아님', '\n'.join(by_kind['exceptions']))
        self.assertEqual(result.rules, ())
        self.assertIn('overlap', {m.field for m in result.missing if m.document_id == 'doc-2'})
        before_prose_read = json.loads(scripted.messages[2][-1]['content'])
        self.assertEqual(before_prose_read['counts']['facts']['doc-2']['eligibility'], 0)

    def test_unread_prose_qualifier_still_requires_another_read_before_extraction(self):
        text = '지원대상\n가상 지역 주민\n단, 입주 예정자는 별도 확인 필요'
        scripted = ScriptedChat([
            reply(fetch('doc-1', 1, 2)), reply(extract('doc-1', 'eligibility', 2, 2)),
            reply(fetch('doc-1', 3, 3)), reply(call('check_missing')),
            reply(call('save_candidate')),
        ])
        result = analyze_policy(make_input((text,)), chat=scripted)
        self.assertEqual(result.trace[1].outcome, 'invalid')
        feedback = tool_payloads(scripted.messages[2])[-1]
        self.assertEqual(feedback['next_tool_arguments'], {
            'doc_id': 'doc-1', 'start_line': 1, 'end_line': 3,
        })
        self.assertEqual(result.facts[0].eligibility[0].quote, text)

    def test_oversized_prose_section_fails_instead_of_dropping_conditions(self):
        text = '\n'.join(['지원대상', *['가상 조건' for _ in range(41)]])
        with self.assertRaisesRegex(ValueError, '^policy_analysis_evidence_context_limit$'):
            analyze_policy(make_input((text,)), chat=ScriptedChat([reply(fetch('doc-1', 1, 42))]))

    def test_unshown_expanded_api_context_can_be_read_before_retrying(self):
        text = '[addAplyQlfcCndCn]\n만 19세 이상 청년 대상\n가상 지역 거주 또는 지역 대학 재학생'
        scripted = ScriptedChat([
            reply(fetch('doc-1', 2, 2)),
            reply(extract('doc-1', 'eligibility', 2, 2)),
            reply(fetch('doc-1', 1, 3)),
            reply(extract('doc-1', 'eligibility', 2, 2)),
            reply(call('check_missing')), reply(call('save_candidate')),
        ])

        def chat(messages, tools):
            if len(scripted.messages) == 2:
                feedback = tool_payloads(messages)[-1]
                self.assertEqual(feedback['error'], 'evidence_not_previously_shown')
                self.assertEqual(feedback['next_tool'], 'fetch_document')
                self.assertEqual(feedback['next_tool_arguments'], {
                    'doc_id': 'doc-1', 'start_line': 1, 'end_line': 3,
                })
                self.assertNotIn('가상 지역', json.dumps(feedback, ensure_ascii=False))
            return scripted(messages, tools)

        result = analyze_policy(make_input((text,)), chat=chat)
        self.assertEqual(result.trace[1].outcome, 'invalid')
        self.assertEqual(result.trace[-1].outcome, 'needs_review')
        self.assertEqual(len(result.facts[0].eligibility), 1)
        self.assertEqual(result.facts[0].eligibility[0].quote, text)
        self.assertEqual(result.facts[0].eligibility[0].location, 'lines 1-3')

    def test_incomplete_check_requires_recheck_after_reading_remaining_lines(self):
        scripted = ScriptedChat([
            reply(fetch('doc-1', 1, 2)), reply(call('check_missing')),
            reply(fetch('doc-1', 3, 7)), reply(call('save_candidate')),
            reply(call('check_missing')), reply(call('save_candidate')),
        ])

        def chat(messages, tools):
            if len(scripted.messages) == 4:
                feedback = tool_payloads(messages)[-1]
                self.assertEqual(feedback['error'], 'coverage_incomplete')
                self.assertEqual(feedback['next_tool'], 'check_missing')
                self.assertIn('again', feedback['instruction'])
            return scripted(messages, tools)

        result = analyze_policy(make_input((DOC_ONE,)), chat=chat)
        self.assertEqual(result.trace[3].outcome, 'invalid')
        self.assertEqual(result.trace[-1].outcome, 'needs_review')

    def test_fact_review_pair_round_trips_original_line_endings(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from benefitfinder.application.policy_rules import PolicyReference, RuleReviewBatch
        from benefitfinder.infrastructure.policy_rule_files import (
            load_f12_review_pair, write_f12_review_pair,
        )
        from benefitfinder.ingestion.rule_review_workflow import prepare_snapshot

        for newline in ('\n', '\r\n'):
            with self.subTest(newline=repr(newline)), TemporaryDirectory() as directory:
                text = newline.join(('가상 주거 지원 안내', '지원금은 최대 30만원입니다.',
                                     '신청인 부담금 1천원을 제외합니다.', '', '신청 안내'))
                input = make_input((text,))
                result = analyze_policy(input, chat=ScriptedChat([
                    reply(fetch('doc-1', 1, 5)),
                    reply(extract('doc-1', 'amount', 2, 4)),
                    reply(call('check_missing')), reply(call('save_candidate')),
                ]))
                policies = tuple(PolicyReference(p.id, 'youth', p.categories) for p in input.policies)
                snapshot = prepare_snapshot(RuleReviewBatch(input.documents, policies, ()), at=NOW)
                path = Path(directory) / 'review'
                write_f12_review_pair(path, snapshot, result.facts[0])
                stored, facts = load_f12_review_pair(path)
                self.assertEqual(stored.batch.documents, input.documents)
                self.assertEqual(facts, result.facts[0])
                self.assertEqual(facts.document.text, text)
                citation = facts.amounts[0].evidence
                self.assertEqual(citation.quote, newline.join(text.split(newline)[1:3]))
                self.assertEqual(citation.location, 'lines 2-4')
                self.assertEqual(citation.content_sha256, input.documents[0].content_sha256)
                self.assertIn(citation.quote, text)
                self.assertEqual(facts.amounts[0].amounts_won, (300000,))

    def test_crlf_fact_quote_rejects_interior_newline_normalization(self):
        from dataclasses import replace
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from benefitfinder.application.policy_facts import PolicyAmountCandidate
        from benefitfinder.infrastructure.policy_rule_files import RuleReviewFileError, write_policy_facts

        input = make_input(('가상 공고\r\n최대 30만원 지원\r\n본인 부담금 1천원 제외\r\n신청 안내',))
        result = analyze_policy(input, chat=ScriptedChat([
            reply(fetch('doc-1', 1, 4)), reply(extract('doc-1', 'amount', 2, 3)),
            reply(call('check_missing')), reply(call('save_candidate')),
        ]))
        draft = result.facts[0]
        citation = draft.amounts[0].evidence
        altered = replace(citation, quote=citation.quote.replace('\r\n', '\n'))
        with TemporaryDirectory() as directory, self.assertRaises(RuleReviewFileError):
            write_policy_facts(Path(directory) / 'altered.json', replace(draft, amounts=(
                PolicyAmountCandidate(altered, draft.amounts[0].amounts_won),
            )))

    def test_tool_history_is_serializable_by_the_actual_compatible_transport(self):
        from benefitfinder.infrastructure.compatible_llm import _wire_messages
        scripted = ScriptedChat([
            reply({'id': 'actual-provider-call-id', **fetch('doc-1', 1, 7)}),
            reply(call('check_missing')), reply(call('save_candidate')),
        ])
        def chat(messages, tools):
            _wire_messages(messages)
            return scripted(messages, tools)
        result = analyze_policy(make_input((DOC_ONE,)), chat=chat)
        self.assertEqual(len(result.trace), 3)

    def test_same_quote_with_different_rule_directions_has_distinct_candidate_ids(self):
        scripted = ScriptedChat([
            reply(fetch('doc-1', 1, 7), call('lookup_policy', name='달빛')),
            reply(extract('doc-1', 'conditional', 5, 6, target_id=TARGET_ID,
                          direction='both', exception_lines=[6]),
                  extract('doc-1', 'conditional', 5, 6, target_id=TARGET_ID,
                          direction='source_to_target', exception_lines=[6])),
            reply(call('check_missing')), reply(call('save_candidate')),
        ])
        result = analyze_policy(make_input((DOC_ONE,)), chat=scripted)
        self.assertEqual(len({rule.id for rule in result.rules}), 2)

    def test_multi_document_tools_coverage_missing_rules_and_trace(self) -> None:
        chat = ScriptedChat(
            [
                reply(call("find_clauses", doc_id="doc-1", query="달빛")),
                reply(fetch("doc-2", 1, 5)),
                reply(fetch("doc-3", 1, 1)),
                reply(call("lookup_policy", name="달빛")),
                reply(
                    extract("doc-1", "eligibility", 2, 2),
                    extract("doc-1", "amount", 3, 3),
                    extract("doc-1", "timing", 7, 7),
                    extract("doc-1", "application", 4, 4),
                ),
                reply(
                    extract("doc-1", "exceptions", 6, 6),
                    extract("doc-2", "service", 5, 5),
                    extract("doc-2", "eligibility", 2, 2),
                    extract("doc-2", "application", 3, 3),
                ),
                reply(
                    extract("doc-2", "timing", 4, 4),
                    extract(
                        "doc-1",
                        "conditional",
                        5,
                        6,
                        target_id=TARGET_ID,
                        direction=RuleDirection.UNKNOWN.value,
                        condition_lines=[5],
                        exception_lines=[6],
                    ),
                ),
                reply(call("check_missing")),
                reply(call("find_clauses", doc_id="doc-2", query="없는 표현")),
                reply(call("check_missing")),
                reply(fetch("doc-2", 1, 5)),
                reply(call("check_missing")),
                reply(call("check_missing")),
                reply(call("save_candidate")),
            ]
        )
        observed: list[AnalysisStep] = []

        result = analyze_policy(make_input(), chat=chat, on_step=observed.append)

        self.assertEqual([fact.document.id for fact in result.facts], ["doc-1", "doc-2", "doc-3"])
        self.assertEqual(result.facts[2].eligibility, ())
        self.assertEqual(result.facts[2].amounts, ())
        self.assertEqual(result.facts[2].payment_timing, ())
        self.assertIsNone(result.facts[2].scheduled_monthly_day)
        self.assertEqual(result.facts[0].amounts[0].amounts_won, (100_000,))
        self.assertEqual(
            [(item.document_id, item.field) for item in result.missing],
            [
                ("doc-2", "amount"),
                ("doc-3", "age"),
                ("doc-3", "amount"),
                ("doc-3", "application"),
                ("doc-3", "timing"),
                ("doc-2", "overlap"),
                ("doc-3", "overlap"),
            ],
        )
        self.assertEqual([item.kind for item in result.additional], [
            "application",
            "exceptions",
            "service",
            "application",
        ])
        (rule,) = result.rules
        source_quote = "\n".join(DOC_ONE.splitlines()[4:6])
        self.assertEqual(rule.kind, RuleKind.CONDITIONAL)
        self.assertEqual(rule.direction, RuleDirection.UNKNOWN)
        self.assertEqual(rule.target_policy_ids, (TARGET_ID,))
        self.assertEqual(rule.description, source_quote)
        self.assertEqual(rule.citations[0].quote, source_quote)
        self.assertEqual(rule.citations[0].content_sha256, make_input().documents[0].content_sha256)
        self.assertEqual(rule.conditions, (DOC_ONE.splitlines()[4],))
        self.assertEqual(rule.exceptions, (DOC_ONE.splitlines()[5],))
        self.assertIn(source_quote, rule.unresolved)
        self.assertTrue(any('예외' in value for value in rule.unresolved))
        self.assertEqual(rule.citations[0].location, "lines 5-6")

        self.assertEqual(observed, list(result.trace))
        self.assertTrue(all(left is right for left, right in zip(observed, result.trace, strict=True)))
        self.assertEqual(
            {step.tool for step in result.trace},
            {"fetch_document", "find_clauses", "lookup_policy", "extract_rule", "check_missing", "save_candidate"},
        )
        self.assertEqual(result.trace[-1].outcome, "needs_review")
        self.assertIn("not_found", {step.outcome for step in result.trace})
        for step in result.trace:
            self.assertEqual(set(asdict(step)), {"tool", "outcome", "elapsed_ms"})
            self.assertRegex(step.outcome, r"^[a-z][a-z0-9_]{0,63}$")
            self.assertIs(type(step.elapsed_ms), int)
            self.assertGreaterEqual(step.elapsed_ms, 0)
            self.assertLessEqual(step.elapsed_ms, 3_600_000)
            self.assertNotIn("가상 달빛 수당과 동시에", repr(asdict(step)))

        self.assertEqual(
            {item["function"]["name"] for item in chat.tools[0]},
            {"fetch_document", "find_clauses", "lookup_policy", "extract_rule", "check_missing", "save_candidate"},
        )
        initial_metadata = chat.messages[0][1]["content"]
        self.assertNotIn("신청 기간은 상시 접수", initial_metadata)
        first_find = tool_payloads(chat.messages[1])[0]
        self.assertEqual(first_find["matches"][0]["match_line"], 5)
        self.assertIn("달빛", first_find["matches"][0]["context"][4]["text"])

    def test_same_turn_and_unshown_or_out_of_range_evidence_are_fed_back(self) -> None:
        chat = ScriptedChat(
            [
                reply(fetch("doc-1", 1, 2), extract("doc-1", "eligibility", 1, 1)),
                reply(extract("doc-1", "eligibility", 2, 8)),
                reply(call("not_a_tool")),
                RuntimeError("private model failure"),
            ]
        )
        steps: list[AnalysisStep] = []
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr), self.assertRaisesRegex(
            ValueError, "^policy_analysis_model_failed$"
        ):
            analyze_policy(make_input(), chat=chat, on_step=steps.append)

        errors = [
            payload["error"]
            for payload in tool_payloads(chat.messages[-1])
            if "error" in payload
        ]
        self.assertEqual(
            errors,
            ["evidence_not_previously_shown", "line_range_invalid", "unknown_tool"],
        )
        self.assertEqual([step.outcome for step in steps], ["shown", "invalid", "invalid", "invalid"])
        self.assertEqual(steps[2].tool, "extract_rule")
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")

    def test_ambiguous_names_are_not_linked_to_a_rule(self) -> None:
        policies = (
            AnalysisPolicy(SOURCE_ID, "가상 하늘 통합 지원", ("employment", "housing")),
            AnalysisPolicy(TARGET_ID, "가상 달빛 수당", ("employment",), ("공통 별칭",)),
            AnalysisPolicy(OTHER_ID, "가상 햇빛 수당", ("housing",), ("공통 별칭",)),
        )
        chat = ScriptedChat(
            [
                reply(fetch("doc-1", 1, 7)),
                reply(call("lookup_policy", name="공통 별칭")),
                reply(
                    extract(
                        "doc-1",
                        "simultaneous_exclusion",
                        5,
                        5,
                        target_id=TARGET_ID,
                        direction="both",
                    )
                ),
                reply(
                    extract("doc-1", "eligibility", 2, 2),
                    extract("doc-1", "amount", 3, 3),
                    extract("doc-1", "application", 4, 4),
                    extract("doc-1", "timing", 7, 7),
                ),
                reply(call("check_missing")),
                reply(call("save_candidate")),
            ]
        )

        result = analyze_policy(make_input(texts=(DOC_ONE,), policies=policies), chat=chat)

        self.assertEqual(result.rules, ())
        lookup = next(
            payload
            for payload in tool_payloads(chat.messages[-1])
            if payload.get("status") == "ambiguous"
        )
        self.assertEqual(lookup["matching_policy_ids"], sorted([TARGET_ID, OTHER_ID]))
        self.assertIn("invalid", {step.outcome for step in result.trace})

    def test_save_requires_complete_coverage_and_a_check_of_the_latest_draft(self) -> None:
        chat = ScriptedChat(
            [
                reply(call("check_missing")),
                reply(call("save_candidate")),
                reply(fetch("doc-1", 1, 7)),
                reply(extract("doc-1", "eligibility", 2, 2)),
                reply(call("save_candidate")),
                reply(call("check_missing")),
                reply(call("save_candidate")),
            ]
        )

        result = analyze_policy(make_input(texts=(DOC_ONE,)), chat=chat)

        payloads = tool_payloads(chat.messages[-1])
        errors = [payload["error"] for payload in payloads if "error" in payload]
        self.assertEqual(errors, ["coverage_incomplete", "check_stale"])
        self.assertIn('timing', {item.field for item in result.missing})
        self.assertEqual(result.missing[-1].field, 'overlap')
        self.assertEqual(result.trace[-1].outcome, "needs_review")


    def test_large_window_and_callback_failure_do_not_leak_or_finish(self) -> None:
        text = "\n".join("가" * 200 for _ in range(50))
        chat = ScriptedChat(
            [
                reply(fetch("doc-1", 1, 50)),
                reply(fetch("doc-1", 1, 10)),
                RuntimeError("private source-like failure"),
            ]
        )
        steps: list[AnalysisStep] = []
        with self.assertRaisesRegex(ValueError, "^policy_analysis_model_failed$"):
            analyze_policy(make_input(texts=(text,)), chat=chat, on_step=steps.append)
        errors = [
            payload["error"]
            for payload in tool_payloads(chat.messages[-1])
            if "error" in payload
        ]
        self.assertEqual(errors, ["window_too_large"])
        self.assertEqual([step.outcome for step in steps], ["invalid", "shown"])

        class CallbackFailure(Exception):
            pass

        def fail(_: AnalysisStep) -> None:
            raise CallbackFailure("private callback detail")

        callback_chat = ScriptedChat([reply(fetch("doc-1", 1, 1))])
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr), self.assertRaises(CallbackFailure):
            analyze_policy(make_input(), chat=callback_chat, on_step=fail)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")

    def test_timeout_model_call_limit_and_response_call_limit_are_fixed(self) -> None:
        class SlowChat(ScriptedChat):
            def __call__(self, messages, tools):
                self.messages.append(deepcopy(messages))
                sleep(0.06)
                return reply(fetch("doc-1", 1, 1))

        slow = SlowChat([])
        with self.assertRaisesRegex(ValueError, "^f12_analysis_timeout$"):
            analyze_policy(make_input(), chat=slow, budget_seconds=0.05)
        self.assertEqual(len(slow.messages), 1)

        class RepeatingFetch(ScriptedChat):
            def __call__(self, messages, tools):
                self.messages.append(deepcopy(messages))
                return reply(fetch("doc-1", 1, 1))

        repeating = RepeatingFetch([])
        steps: list[AnalysisStep] = []
        with self.assertRaisesRegex(ValueError, "^policy_analysis_model_call_limit$"):
            analyze_policy(make_input(), chat=repeating, on_step=steps.append)
        self.assertEqual(len(repeating.messages), 48)
        self.assertEqual(len(steps), 48)

        too_many = ScriptedChat(
            [reply(*(fetch("doc-1", 1, 1) for _ in range(5)))]
        )
        with self.assertRaisesRegex(ValueError, "^policy_analysis_tool_call_limit$"):
            analyze_policy(make_input(), chat=too_many)

    def test_fact_limit_fails_without_returning_a_truncated_result(self) -> None:
        text = "\n".join("만 19세" for _ in range(101))

        class OverflowChat(ScriptedChat):
            def __call__(self, messages, tools):
                self.messages.append(deepcopy(messages))
                if len(self.messages) == 1:
                    return reply(fetch("doc-1", 1, 101))
                start = 1 + (len(self.messages) - 2) * 4
                end = min(101, start + 3)
                return reply(
                    *(
                        extract("doc-1", "eligibility", line, line)
                        for line in range(start, end + 1)
                    )
                )

        with self.assertRaisesRegex(
            ValueError, "^policy_analysis_(?:work|evidence)_limit$"
        ):
            analyze_policy(make_input(texts=(text,)), chat=OverflowChat([]))


if __name__ == "__main__":
    import unittest

    unittest.main()


class ApiEvidenceRegressionTests(TestCase):
    def analyze(self, text, *selections):
        return analyze_policy(make_input((text,)), chat=ScriptedChat([
            reply(fetch('doc-1', 1, len(text.splitlines()))),
            *[reply(selection) for selection in selections],
            reply(call('check_missing')), reply(call('save_candidate')),
        ]))

    def test_application_program_and_raw_income_are_not_payment_facts(self):
        text = ('[aplyYmd]\n20300101~20301231\n\n'
                '[bizPrdBgngYmd]\n20300101\n\n'
                '[earnMinAmt]\n0\n\n[sprtTrgtAgeLmtYn]\nY\n\n'
                '[sprtTrgtMinAge]\n21\n\n[sprtTrgtMaxAge]\n37')
        result = self.analyze(text, extract('doc-1', 'timing', 2, 2),
                              extract('doc-1', 'amount', 8, 8))
        facts = result.facts[0]
        self.assertFalse(facts.amounts)
        self.assertFalse(facts.payment_timing)
        missing = {m.field for m in result.missing}
        self.assertNotIn('application', missing)
        self.assertNotIn('age', missing)
        self.assertIn('age_interpretation', missing)
        self.assertIn('amount', missing)
        self.assertIn('timing', missing)
        self.assertIn('program_period', {e.kind for e in result.additional})

    def test_support_preserves_range_type_duration_condition_and_excludes_costs(self):
        text = ('[detailSupport]\nI유형: 구직활동 이행시 월 73~117만원, 7개월 지원\n'
                '본인부담금 2천원 제외 금액 지급\n거래금액 2억원 이하\n'
                '신청인 중개보수 우선 지급 후 증빙서류 제출 시 지원')
        result = self.analyze(text, extract('doc-1', 'amount', 2, 2))
        facts = result.facts[0]
        self.assertEqual(facts.amounts[0].amounts_won, (730000, 1170000))
        self.assertEqual(facts.amounts[0].evidence.quote, text)
        self.assertIn('7개월', facts.payment_timing[0].quote)
        self.assertIn('우선 지급 후', facts.payment_timing[0].quote)
        self.assertIsNone(facts.scheduled_monthly_day)

    def test_formula_and_compound_eligibility_remain_in_exact_field_context(self):
        text = ('[detailTarget]\r\n구직자이면서 (지역 거주 또는 대학 재학)\r\n'
                '주택 거래금액 2억원 이하\r\n※거래금액=임차보증금+(월세액*100)\r\n'
                '\r\n[detailMethod]\r\n온라인 또는 방문')
        result = self.analyze(text, extract('doc-1', 'eligibility', 3, 3))
        citation = result.facts[0].eligibility[0]
        self.assertIn('또는 대학 재학', citation.quote)
        self.assertIn('월세액*100', citation.quote)
        self.assertIn('\r\n', citation.quote)
        self.assertNotIn('[detailMethod]', citation.quote)
        self.assertEqual(citation.content_sha256, make_input((text,)).documents[0].content_sha256)

    def test_service_duration_does_not_become_eligibility_or_payment(self):
        text = '[detailSupport]\n가상 인턴형 일경험 2~4개월 내외\n가상 프로젝트형 3개월 이내'
        result = self.analyze(text, extract('doc-1', 'eligibility', 2, 2),
                              extract('doc-1', 'timing', 2, 2))
        self.assertFalse(result.facts[0].eligibility)
        self.assertFalse(result.facts[0].payment_timing)
        self.assertEqual(result.additional[0].kind, 'service')
        self.assertIn('내외', result.additional[0].evidence.quote)

    def test_context_must_be_read_before_model_can_extract_it(self):
        text = '[detailTarget]\n지역 거주자 또는 대학 재학생\n단, 휴학생 포함'
        chat = ScriptedChat([
            reply(fetch('doc-1', 2, 2)), reply(extract('doc-1', 'eligibility', 2, 2)),
            reply(fetch('doc-1', 1, 3)), reply(call('check_missing')),
            reply(call('save_candidate')),
        ])
        result = analyze_policy(make_input((text,)), chat=chat)
        self.assertEqual(result.trace[1].outcome, 'invalid')
        self.assertIn('휴학생 포함', result.facts[0].eligibility[0].quote)

    def test_allowance_name_alone_is_not_a_payment_schedule(self):
        text = '[detailSupport]\n맞춤형 프로그램 및 참여수당, 인센티브 제공\n이수 시 취업서비스 연계'
        result = self.analyze(text, extract('doc-1', 'timing', 2, 2))
        self.assertFalse(result.facts[0].payment_timing)
        self.assertIn('timing', {m.field for m in result.missing})
