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
        self.assertEqual(rule.unresolved, (source_quote,))
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
        self.assertEqual(result.missing[-1].field, "timing")
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
