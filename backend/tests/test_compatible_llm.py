"""Focused tests for the OpenAI-compatible HTTP adapter."""

from __future__ import annotations

import contextlib
import io
import json
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from benefitfinder.infrastructure.compatible_llm import (
    CHAT_FAILED,
    COMPLETION_FAILED,
    INVALID_ENDPOINT,
    CompatibleChat,
    CompatibleExplanationCompletion,
    LLMEndpoint,
)

SECRET = "VIRTUAL-SECRET-compatible-llm"
MAX_BYTES = 1024 * 1024
MODULE = "benefitfinder.infrastructure.compatible_llm"


class FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body

    def read(self, amount: int = -1) -> bytes:
        if amount >= 0:
            return self.body[:amount]
        return self.body


class FakeConnection:
    def __init__(
        self,
        response: FakeResponse | None = None,
        *,
        request_error: Exception | None = None,
        response_error: Exception | None = None,
        read_error: Exception | None = None,
    ) -> None:
        self.response = response
        self.request_error = request_error
        self.response_error = response_error
        self.read_error = read_error
        self.request_args: tuple[tuple[object, ...], dict[str, object]] | None = None
        self.closed = False

    def request(self, *args: object, **kwargs: object) -> None:
        if self.request_error is not None:
            raise self.request_error
        self.request_args = (args, kwargs)

    def getresponse(self) -> FakeResponse:
        if self.response_error is not None:
            raise self.response_error
        assert self.response is not None
        if self.read_error is not None:
            response = self.response

            def failing_read(_: int = -1) -> bytes:
                raise self.read_error

            response.read = failing_read  # type: ignore[method-assign]
        return self.response

    def close(self) -> None:
        self.closed = True


def chat_body(
    content: object = "",
    *,
    finish_reason: str = "stop",
    tool_calls: object = None,
    role: object = "assistant",
) -> bytes:
    message: dict[str, object] = {"role": role, "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return json.dumps(
        {
            "choices": [
                {
                    "finish_reason": finish_reason,
                    "message": message,
                }
            ]
        }
    ).encode()


def request_json(connection: FakeConnection) -> dict[str, object]:
    assert connection.request_args is not None
    _, kwargs = connection.request_args
    return json.loads(kwargs["body"])


def request_headers(connection: FakeConnection) -> dict[str, str]:
    assert connection.request_args is not None
    _, kwargs = connection.request_args
    return kwargs["headers"]  # type: ignore[return-value]


class EndpointTests(TestCase):
    def test_local_and_remote_endpoint_rules_hide_key(self) -> None:
        local = LLMEndpoint("http://[::1]:8080/gateway/v1/", "llama-virtual", SECRET)
        remote = LLMEndpoint(
            "https://api.example.test/v1",
            "llama-virtual",
            SECRET,
            local=False,
        )
        self.assertNotIn(SECRET, repr(local))
        self.assertNotIn(SECRET, repr(remote))

        invalid = [
            ("http://example.test/v1", None, True),
            ("http://localhost:8080/v1", SECRET, False),
            ("https://api.example.test/v1", None, False),
            ("https://api.example.test/v1", "", False),
            ("http://localhost:8080/v1?token=x", None, True),
            ("http://localhost:8080/v1#fragment", None, True),
            ("http://user@localhost:8080/v1", None, True),
            ("http://localhost:8080/api", None, True),
            ("http://localhost:8080/v1/../v1", None, True),
            ("http://localhost:8080/v1/%2e%2e", None, True),
            ("http://localhost:8080/v1//", None, True),
        ]
        for base_url, key, local_flag in invalid:
            with self.subTest(base_url=base_url), self.assertRaises(ValueError) as raised:
                LLMEndpoint(base_url, "llama-virtual", key, local=local_flag)
            self.assertEqual(str(raised.exception), INVALID_ENDPOINT)
            self.assertNotIn(SECRET, str(raised.exception))
            self.assertNotIn(base_url, str(raised.exception))

    def test_endpoint_constructor_does_not_connect(self) -> None:
        with patch(f"{MODULE}.http.client.HTTPConnection") as connection:
            LLMEndpoint("http://127.0.0.1:8080", "llama-virtual")
        connection.assert_not_called()


class CompatibleChatTests(TestCase):
    def test_required_tools_requests_serial_tool_calls_without_forcing_a_tool_name(self):
        response_calls = [{'id': 'virtual-call', 'type': 'function', 'function': {
            'name': 'fetch_document', 'arguments': '{"doc_id":"virtual-doc"}'}}]
        connection = FakeConnection(FakeResponse(200, chat_body(
            None, finish_reason='tool_calls', tool_calls=response_calls)))
        endpoint = LLMEndpoint('http://localhost:8080/v1', 'llama-virtual')
        tools = [{'type': 'function', 'function': {'name': 'fetch_document',
                  'parameters': {'type': 'object'}}}]
        with patch(f'{MODULE}.http.client.HTTPConnection', return_value=connection):
            result = CompatibleChat(endpoint, require_tool_calls=True)(
                [{'role': 'user', 'content': '가상 문서 분석'}], tools)
        payload = request_json(connection)
        self.assertEqual(payload['tool_choice'], 'required')
        self.assertIs(payload['parallel_tool_calls'], False)
        self.assertEqual(result['tool_calls'][0]['function']['name'], 'fetch_document')

    def test_required_tools_rejects_empty_tool_list_before_http(self):
        endpoint = LLMEndpoint('http://localhost:8080/v1', 'llama-virtual')
        with patch(f'{MODULE}.http.client.HTTPConnection') as connection:
            with self.assertRaisesRegex(ValueError, CHAT_FAILED):
                CompatibleChat(endpoint, require_tool_calls=True)(
                    [{'role': 'user', 'content': '가상 문서'}], [])
        connection.assert_not_called()

    def test_local_chat_uses_http_path_and_default_timeout(self) -> None:
        connection = FakeConnection(FakeResponse(200, chat_body("virtual answer")))
        endpoint = LLMEndpoint("http://127.0.0.1:8080/gateway/v1", "llama-virtual")

        with (
            patch(f"{MODULE}.http.client.HTTPConnection", return_value=connection) as http,
            patch(f"{MODULE}.http.client.HTTPSConnection") as https,
        ):
            result = CompatibleChat(endpoint)(
                [{"role": "user", "content": "virtual question"}],
                [],
            )

        self.assertEqual(
            result, {"role": "assistant", "content": "virtual answer", "tool_calls": []}
        )
        http.assert_called_once_with("127.0.0.1", 8080, timeout=60.0)
        https.assert_not_called()
        assert connection.request_args is not None
        args, kwargs = connection.request_args
        self.assertEqual(args, ("POST", "/gateway/v1/chat/completions"))
        self.assertNotIn("Authorization", kwargs["headers"])
        self.assertTrue(connection.closed)

    def test_remote_chat_uses_tls_auth_and_timeout_override(self) -> None:
        connection = FakeConnection(FakeResponse(200, chat_body("remote answer")))
        endpoint = LLMEndpoint(
            "https://api.example.test/gateway/v1",
            "llama-virtual",
            SECRET,
            local=False,
        )

        with (
            patch(f"{MODULE}.http.client.HTTPConnection") as http,
            patch(f"{MODULE}.http.client.HTTPSConnection", return_value=connection) as https,
        ):
            result = CompatibleChat(endpoint)(
                [{"role": "user", "content": "virtual question"}],
                [],
                timeout_seconds=4.5,
            )

        self.assertEqual(result["content"], "remote answer")
        http.assert_not_called()
        https.assert_called_once_with("api.example.test", 443, timeout=4.5)
        self.assertEqual(request_headers(connection)["Authorization"], f"Bearer {SECRET}")

    def test_tool_wire_conversion_correlates_multiple_pending_calls_by_name(self) -> None:
        response_calls = [
            {
                "id": "call-response",
                "type": "function",
                "function": {
                    "name": "find_clauses",
                    "arguments": '{"query":"가상 정책"}',
                },
            },
            {
                "function": {
                    "name": "submit_rule",
                    "arguments": '{"items":[1]}',
                }
            },
        ]
        connection = FakeConnection(
            FakeResponse(
                200,
                chat_body(None, finish_reason="tool_calls", tool_calls=response_calls),
            )
        )
        endpoint = LLMEndpoint("http://localhost:8080/v1", "llama-virtual")
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-known",
                        "type": "function",
                        "function": {
                            "name": "find_clauses",
                            "arguments": {"query": "가상 정책"},
                        },
                    },
                    {
                        "function": {
                            "name": "lookup_policy",
                            "arguments": {"name": "가상 정책"},
                        }
                    },
                ],
            },
            {"role": "tool", "tool_name": "lookup_policy", "content": '{"ok":true}'},
            {"role": "tool", "tool_name": "find_clauses", "content": '{"lines":[1]}'},
        ]
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "find_clauses",
                    "parameters": {"type": "object"},
                },
            }
        ]

        with patch(f"{MODULE}.http.client.HTTPConnection", return_value=connection):
            result = CompatibleChat(endpoint)(messages, tools)

        payload = request_json(connection)
        wire_messages = payload["messages"]
        self.assertEqual(wire_messages[0]["content"], "")
        self.assertEqual(
            wire_messages[0]["tool_calls"][0]["function"]["arguments"],
            '{"query":"가상 정책"}',
        )
        self.assertEqual(wire_messages[0]["tool_calls"][1]["id"], "call_1")
        self.assertEqual(wire_messages[1]["tool_call_id"], "call_1")
        self.assertEqual(wire_messages[2]["tool_call_id"], "call-known")
        self.assertNotIn("tool_name", wire_messages[1])
        self.assertEqual(payload["tools"], tools)
        self.assertEqual(result["content"], "")
        self.assertEqual(
            result["tool_calls"][0]["function"]["arguments"],
            {"query": "가상 정책"},
        )
        self.assertEqual(result["tool_calls"][1]["id"], "call_1")

    def test_null_content_is_normalized(self) -> None:
        connection = FakeConnection(FakeResponse(200, chat_body(None)))
        endpoint = LLMEndpoint("http://localhost:8080/v1", "llama-virtual")

        with patch(f"{MODULE}.http.client.HTTPConnection", return_value=connection):
            result = CompatibleChat(endpoint)(
                [{"role": "user", "content": "virtual"}],
                [],
            )

        self.assertEqual(result, {"role": "assistant", "content": "", "tool_calls": []})

    def test_invalid_internal_shapes_fail_before_connecting(self) -> None:
        endpoint = LLMEndpoint("http://localhost:8080/v1", "llama-virtual")
        invalid_messages = [
            [{"role": "developer", "content": "virtual"}],
            [{"role": "user", "content": 1}],
            [{"role": "tool", "tool_name": "find_clauses", "content": "{}"}],
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "find_clauses",
                                "arguments": "{}",
                            }
                        }
                    ],
                }
            ],
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "duplicate",
                            "function": {"name": "find_clauses", "arguments": {}},
                        },
                        {
                            "id": "duplicate",
                            "function": {"name": "lookup_policy", "arguments": {}},
                        },
                    ],
                },
                {"role": "tool", "tool_name": "find_clauses", "content": "{}"},
                {"role": "tool", "tool_name": "lookup_policy", "content": "{}"},
            ],
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {"name": "find_clauses", "arguments": {}},
                        }
                    ],
                }
            ],
        ]
        for messages in invalid_messages:
            with (
                self.subTest(messages=messages),
                patch(f"{MODULE}.http.client.HTTPConnection") as connection,
                self.assertRaises(ValueError) as raised,
            ):
                CompatibleChat(endpoint)(messages, [])
            self.assertEqual(str(raised.exception), CHAT_FAILED)
            connection.assert_not_called()

    def test_failures_use_fixed_errors_and_apply_response_limit(self) -> None:
        endpoint = LLMEndpoint("http://localhost:8080/v1", "llama-virtual")
        duplicate_arguments = chat_body(
            "",
            finish_reason="tool_calls",
            tool_calls=[
                {
                    "id": "call-1",
                    "function": {
                        "name": "find_clauses",
                        "arguments": '{"query":"a","query":"b"}',
                    },
                }
            ],
        )
        malformed_arguments = chat_body(
            "",
            finish_reason="tool_calls",
            tool_calls=[
                {
                    "id": "call-1",
                    "function": {
                        "name": "find_clauses",
                        "arguments": "{bad",
                    },
                }
            ],
        )
        cases = [
            FakeConnection(FakeResponse(500, SECRET.encode())),
            FakeConnection(FakeResponse(200, b"x" * (MAX_BYTES + 1))),
            FakeConnection(FakeResponse(200, b'{"choices":[],"choices":[]}')),
            FakeConnection(FakeResponse(200, b"{bad")),
            FakeConnection(FakeResponse(200, chat_body("cut", finish_reason="length"))),
            FakeConnection(FakeResponse(200, chat_body("wrong role", role="user"))),
            FakeConnection(FakeResponse(200, duplicate_arguments)),
            FakeConnection(FakeResponse(200, malformed_arguments)),
            FakeConnection(
                FakeResponse(200, chat_body(None, finish_reason="tool_calls", tool_calls=[]))
            ),
        ]
        for connection in cases:
            with (
                self.subTest(connection=connection),
                patch(f"{MODULE}.http.client.HTTPConnection", return_value=connection),
                self.assertRaises(ValueError) as raised,
            ):
                CompatibleChat(endpoint)(
                    [{"role": "user", "content": SECRET}],
                    [],
                )
            self.assertEqual(str(raised.exception), CHAT_FAILED)
            self.assertNotIn(SECRET, str(raised.exception))
            self.assertTrue(connection.closed)

        oops = io.StringIO()
        connection = FakeConnection(request_error=OSError(SECRET))
        with (
            patch(f"{MODULE}.http.client.HTTPConnection", return_value=connection),
            contextlib.redirect_stdout(oops),
            contextlib.redirect_stderr(oops),
            self.assertRaises(ValueError) as raised,
        ):
            CompatibleChat(endpoint)(
                [{"role": "user", "content": SECRET}],
                [],
            )
        self.assertEqual(str(raised.exception), CHAT_FAILED)
        self.assertEqual(oops.getvalue(), "")
        self.assertTrue(connection.closed)

        with (
            patch(f"{MODULE}.http.client.HTTPConnection", side_effect=OSError(SECRET)),
            self.assertRaises(ValueError) as raised,
        ):
            CompatibleChat(endpoint)(
                [{"role": "user", "content": SECRET}],
                [],
            )
        self.assertEqual(str(raised.exception), CHAT_FAILED)
        self.assertNotIn(SECRET, str(raised.exception))


class StructuredCompletionTests(IsolatedAsyncioTestCase):
    async def test_complete_forwards_json_schema_and_f13_timeout(self) -> None:
        schema = {
            "type": "object",
            "properties": {"details": {"type": "array"}},
            "required": ["details"],
            "additionalProperties": False,
        }
        connection = FakeConnection(FakeResponse(200, chat_body('{"details":[]}')))
        endpoint = LLMEndpoint("http://127.0.0.1:11434/v1", "llama-virtual")

        with patch(f"{MODULE}.http.client.HTTPConnection", return_value=connection) as http:
            result = await CompatibleExplanationCompletion(endpoint).complete(
                [{"role": "user", "content": "virtual"}],
                schema,
                timeout_seconds=5,
            )

        self.assertEqual(result, {"details": []})
        http.assert_called_once_with("127.0.0.1", 11434, timeout=5.0)
        payload = request_json(connection)
        self.assertEqual(
            payload["response_format"],
            {
                "type": "json_schema",
                "json_schema": {
                    "name": "response",
                    "strict": True,
                    "schema": schema,
                },
            },
        )
        self.assertEqual(payload["max_tokens"], 4096)
        self.assertIs(payload["stream"], False)
        self.assertNotIn("tools", payload)

    async def test_completion_rejects_truncation_duplicate_keys_and_hides_inputs(self) -> None:
        endpoint = LLMEndpoint("http://localhost:8080/v1", "llama-virtual")
        cases = [
            chat_body(SECRET, finish_reason="length"),
            chat_body('{"details":[],"details":[]}'),
            chat_body("{bad"),
            chat_body(None),
            chat_body(SECRET, role="user"),
        ]
        for body in cases:
            connection = FakeConnection(FakeResponse(200, body))
            with (
                self.subTest(body=body),
                patch(f"{MODULE}.http.client.HTTPConnection", return_value=connection),
                self.assertRaises(ValueError) as raised,
            ):
                await CompatibleExplanationCompletion(endpoint).complete(
                    [{"role": "user", "content": SECRET}],
                    {"type": "object"},
                    timeout_seconds=1,
                )
            self.assertEqual(str(raised.exception), COMPLETION_FAILED)
            self.assertNotIn(SECRET, str(raised.exception))
            self.assertTrue(connection.closed)

    def test_chat_complete_json_is_reusable_with_explicit_max_tokens(self) -> None:
        connection = FakeConnection(FakeResponse(200, chat_body('{"value":7}')))
        endpoint = LLMEndpoint("http://localhost:8080/v1", "llama-virtual")
        schema = {"type": "object"}
        with patch(f"{MODULE}.http.client.HTTPConnection", return_value=connection):
            result = CompatibleChat(endpoint).complete_json(
                [{"role": "user", "content": "virtual"}],
                schema,
                timeout_seconds=2,
                max_tokens=123,
            )
        self.assertEqual(result, {"value": 7})
        payload = request_json(connection)
        self.assertEqual(payload["max_tokens"], 123)
        self.assertEqual(payload["response_format"]["json_schema"]["schema"], schema)
