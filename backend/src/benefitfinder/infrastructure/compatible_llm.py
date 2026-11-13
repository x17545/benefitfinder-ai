"""OpenAI-compatible chat and structured-completion HTTP adapter."""

from __future__ import annotations

import asyncio
import http.client
import ipaddress
import json
import math
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

__all__ = [
    "CHAT_FAILED",
    "COMPLETION_FAILED",
    "INVALID_ENDPOINT",
    "CompatibleChat",
    "CompatibleExplanationCompletion",
    "LLMEndpoint",
]

INVALID_ENDPOINT = "compatible_llm_invalid_endpoint"
CHAT_FAILED = "compatible_llm_chat_failed"
COMPLETION_FAILED = "compatible_llm_completion_failed"

_DEFAULT_TIMEOUT_SECONDS = 60.0
_MAX_RESPONSE_BYTES = 1024 * 1024
_MAX_MODEL_CHARS = 256
_MAX_KEY_CHARS = 8192
_MAX_CALL_ID_CHARS = 256
_MAX_TOOL_NAME_CHARS = 128
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_PATH_SEGMENT = re.compile(r"[A-Za-z0-9._~-]+")
_HOST_LABEL = re.compile(r"[A-Za-z0-9-]+")
_CHAT_FINISH_REASONS = frozenset({"stop", "tool_calls"})
_MESSAGE_KEYS = {
    "system": frozenset({"role", "content"}),
    "user": frozenset({"role", "content"}),
    "assistant": frozenset({"role", "content", "tool_calls"}),
    "tool": frozenset({"role", "tool_name", "content"}),
}


class _InvalidResponse(Exception):
    pass


def _has_control(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _ascii_host(host: str) -> str:
    if ":" in host:
        ipaddress.IPv6Address(host)
        return host
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise _InvalidResponse from None
    if len(ascii_host) > 253:
        raise _InvalidResponse
    labels = ascii_host.split(".")
    if any(
        not label
        or not _HOST_LABEL.fullmatch(label)
        or label.startswith("-")
        or label.endswith("-")
        for label in labels
    ):
        raise _InvalidResponse
    return ascii_host.casefold()


def _request_path(path: str) -> str:
    if not path or path == "/":
        return "/v1/chat/completions"
    if "//" in path:
        raise _InvalidResponse
    prefix = path[:-1] if path.endswith("/") else path
    if not prefix.startswith("/") or not prefix.endswith("/v1"):
        raise _InvalidResponse
    segments = prefix[1:].split("/")
    if any(
        not segment or segment in {".", ".."} or not _PATH_SEGMENT.fullmatch(segment)
        for segment in segments
    ):
        raise _InvalidResponse
    return prefix + "/chat/completions"


@dataclass(frozen=True)
class LLMEndpoint:
    """Validated endpoint configuration whose representation omits the API key."""

    base_url: str
    model: str
    api_key: str | None = field(default=None, repr=False)
    local: bool = field(default=True, kw_only=True)
    _scheme: str = field(init=False, repr=False, compare=False)
    _host: str = field(init=False, repr=False, compare=False)
    _port: int = field(init=False, repr=False, compare=False)
    _path: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        try:
            if not isinstance(self.base_url, str) or not 0 < len(self.base_url) <= 2048:
                raise _InvalidResponse
            if _has_control(self.base_url) or "\\" in self.base_url:
                raise _InvalidResponse
            if not isinstance(self.model, str):
                raise _InvalidResponse
            model = self.model.strip()
            if not model or len(model) > _MAX_MODEL_CHARS or _has_control(self.model):
                raise _InvalidResponse
            if type(self.local) is not bool:
                raise _InvalidResponse
            if self.api_key is not None:
                if (
                    not isinstance(self.api_key, str)
                    or not self.api_key.strip()
                    or len(self.api_key) > _MAX_KEY_CHARS
                    or _has_control(self.api_key)
                ):
                    raise _InvalidResponse

            parts = urlsplit(self.base_url)
            scheme = parts.scheme.casefold()
            if (
                scheme not in {"http", "https"}
                or not parts.netloc
                or "@" in parts.netloc
                or parts.query
                or parts.fragment
            ):
                raise _InvalidResponse
            hostname = parts.hostname
            if hostname is None:
                raise _InvalidResponse
            host = _ascii_host(hostname)
            port = parts.port
            if port is None:
                port = 443 if scheme == "https" else 80
            if not 0 < port <= 65535:
                raise _InvalidResponse

            if self.local:
                if scheme not in {"http", "https"} or host not in _LOCAL_HOSTS:
                    raise _InvalidResponse
            elif scheme != "https" or self.api_key is None:
                raise _InvalidResponse

            path = _request_path(parts.path)
        except Exception:
            raise ValueError(INVALID_ENDPOINT) from None

        object.__setattr__(self, "_scheme", scheme)
        object.__setattr__(self, "_host", host)
        object.__setattr__(self, "_port", port)
        object.__setattr__(self, "_path", path)

    def complete_json(
        self,
        messages: list[dict[str, object]],
        schema: dict[str, object],
        *,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        max_tokens: int = 4096,
    ) -> object:
        """Return one strictly parsed JSON response produced with JSON Schema output."""
        try:
            if not isinstance(schema, dict) or not _valid_json_value(schema):
                raise _InvalidResponse
            if type(max_tokens) is not int or max_tokens <= 0:
                raise _InvalidResponse
            payload: dict[str, object] = {
                "model": self.model,
                "messages": _wire_messages(messages),
                "temperature": 0,
                "max_tokens": max_tokens,
                "stream": False,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "response",
                        "strict": True,
                        "schema": schema,
                    },
                },
            }
            data = self._post(payload, timeout_seconds)
            return _structured_content(data)
        except Exception:
            raise ValueError(COMPLETION_FAILED) from None

    def _post(self, payload: dict[str, object], timeout_seconds: object) -> object:
        timeout = _timeout(timeout_seconds)
        body = _json_dumps(payload).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self.api_key is not None:
            headers["Authorization"] = "Bearer " + self.api_key
        connection_type = (
            http.client.HTTPSConnection if self._scheme == "https" else http.client.HTTPConnection
        )
        connection = connection_type(self._host, self._port, timeout=timeout)
        try:
            connection.request("POST", self._path, body=body, headers=headers)
            response = connection.getresponse()
            if response.status != 200:
                raise _InvalidResponse
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
            if not isinstance(raw, bytes) or len(raw) > _MAX_RESPONSE_BYTES:
                raise _InvalidResponse
            return _strict_loads(raw)
        finally:
            try:
                connection.close()
            except Exception:
                pass


class CompatibleChat:
    """Synchronous chat callable conforming to the internal tool-call protocol."""

    def __init__(self, endpoint: LLMEndpoint, *, require_tool_calls: bool = False) -> None:
        if not isinstance(endpoint, LLMEndpoint) or type(require_tool_calls) is not bool:
            raise ValueError(INVALID_ENDPOINT)
        self._endpoint = endpoint
        self._require_tool_calls = require_tool_calls

    def __call__(
        self,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
        *,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> dict[str, object]:
        try:
            payload: dict[str, object] = {
                "model": self._endpoint.model,
                "messages": _wire_messages(messages),
                "temperature": 0,
                "stream": False,
            }
            wire_tools = _wire_tools(tools)
            if wire_tools:
                payload["tools"] = wire_tools
                if self._require_tool_calls:
                    payload['tool_choice'] = 'required'
                    payload['parallel_tool_calls'] = False
            elif self._require_tool_calls:
                raise _InvalidResponse
            data = self._endpoint._post(payload, timeout_seconds)
            return _chat_message(data)
        except Exception:
            raise ValueError(CHAT_FAILED) from None

    def complete_json(
        self,
        messages: list[dict[str, object]],
        schema: dict[str, object],
        *,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        max_tokens: int = 4096,
    ) -> object:
        """Synchronous JSON Schema completion for callers replacing LocalE4BChat."""
        return self._endpoint.complete_json(
            messages,
            schema,
            timeout_seconds=timeout_seconds,
            max_tokens=max_tokens,
        )


class CompatibleExplanationCompletion:
    """Async structured completion backed by the compatible chat endpoint."""

    def __init__(self, endpoint: LLMEndpoint) -> None:
        if not isinstance(endpoint, LLMEndpoint):
            raise ValueError(INVALID_ENDPOINT)
        self._endpoint = endpoint

    async def complete(
        self,
        messages: list[dict[str, object]],
        schema: dict[str, object],
        *,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> object:
        try:
            return await asyncio.to_thread(
                self._endpoint.complete_json,
                messages,
                schema,
                timeout_seconds=timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ValueError(COMPLETION_FAILED) from None


def _timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _InvalidResponse
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0:
        raise _InvalidResponse
    return timeout


def _valid_json_value(value: object, depth: int = 0) -> bool:
    if depth > 64:
        return False
    if value is None or type(value) in {bool, int, str}:
        return True
    if type(value) is float:
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_valid_json_value(item, depth + 1) for item in value)
    if isinstance(value, dict):
        return all(
            type(key) is str and _valid_json_value(item, depth + 1) for key, item in value.items()
        )
    return False


def _json_dumps(value: object) -> str:
    if not _valid_json_value(value):
        raise _InvalidResponse
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _InvalidResponse
        result[key] = value
    return result


def _reject_constant(_: str) -> object:
    raise _InvalidResponse


def _strict_loads(value: str | bytes) -> object:
    return json.loads(
        value,
        object_pairs_hook=_unique_object,
        parse_constant=_reject_constant,
    )


def _call_id(value: object, index: int, used: set[str]) -> str:
    if value is None or value == "":
        candidate = f"call_{index}"
        suffix = 1
        while candidate in used:
            candidate = f"call_{index}_{suffix}"
            suffix += 1
    else:
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > _MAX_CALL_ID_CHARS
            or _has_control(value)
            or value in used
        ):
            raise _InvalidResponse
        candidate = value
    used.add(candidate)
    return candidate


def _assistant_calls(raw_calls: object, *, wire: bool) -> list[dict[str, object]]:
    if raw_calls is None:
        return []
    if not isinstance(raw_calls, list):
        raise _InvalidResponse
    calls: list[dict[str, object]] = []
    used: set[str] = set()
    for index, raw_call in enumerate(raw_calls):
        if not isinstance(raw_call, dict):
            raise _InvalidResponse
        if not set(raw_call) <= {"id", "type", "function"}:
            raise _InvalidResponse
        call_type = raw_call.get("type")
        if call_type not in {None, "function"}:
            raise _InvalidResponse
        function = raw_call.get("function")
        if not isinstance(function, dict) or not set(function) <= {"name", "arguments"}:
            raise _InvalidResponse
        name = function.get("name")
        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name) > _MAX_TOOL_NAME_CHARS
            or _has_control(name)
        ):
            raise _InvalidResponse
        arguments = function.get("arguments")
        if wire:
            if not isinstance(arguments, dict) or not _valid_json_value(arguments):
                raise _InvalidResponse
            encoded_arguments = _json_dumps(arguments)
        else:
            if not isinstance(arguments, str) or not arguments.strip():
                raise _InvalidResponse
            parsed = _strict_loads(arguments)
            if not isinstance(parsed, dict):
                raise _InvalidResponse
            encoded_arguments = arguments
            arguments = parsed
        call_id = _call_id(raw_call.get("id"), index, used)
        calls.append(
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": encoded_arguments if wire else arguments,
                },
            }
        )
    return calls


def _wire_messages(messages: object) -> list[dict[str, object]]:
    if not isinstance(messages, list):
        raise _InvalidResponse
    wire: list[dict[str, object]] = []
    pending: list[tuple[str, str]] = []
    for message in messages:
        if not isinstance(message, dict):
            raise _InvalidResponse
        role = message.get("role")
        if not isinstance(role, str) or role not in _MESSAGE_KEYS:
            raise _InvalidResponse
        if not set(message) <= _MESSAGE_KEYS[role]:
            raise _InvalidResponse
        if pending and role != "tool":
            raise _InvalidResponse

        if role in {"system", "user"}:
            content = message.get("content")
            if not isinstance(content, str):
                raise _InvalidResponse
            wire.append({"role": role, "content": content})
        elif role == "assistant":
            content = message.get("content")
            if content is None:
                content = ""
            if not isinstance(content, str):
                raise _InvalidResponse
            calls = _assistant_calls(message.get("tool_calls"), wire=True)
            wire_message: dict[str, object] = {"role": "assistant", "content": content}
            if calls:
                wire_message["tool_calls"] = calls
                pending = [
                    (str(call["id"]), str(call["function"]["name"]))  # type: ignore[index]
                    for call in calls
                ]
            wire.append(wire_message)
        else:
            name = message.get("tool_name")
            content = message.get("content")
            if not isinstance(name, str) or not isinstance(content, str):
                raise _InvalidResponse
            pending_index = next(
                (index for index, (_, pending_name) in enumerate(pending) if pending_name == name),
                None,
            )
            if pending_index is None:
                raise _InvalidResponse
            tool_call_id, _ = pending.pop(pending_index)
            wire.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": content,
                }
            )
    if pending:
        raise _InvalidResponse
    return wire


def _wire_tools(tools: object) -> list[dict[str, object]]:
    if not isinstance(tools, list) or not _valid_json_value(tools):
        raise _InvalidResponse
    normalized = _strict_loads(_json_dumps(tools))
    if not isinstance(normalized, list):
        raise _InvalidResponse
    for tool in normalized:
        if not isinstance(tool, dict) or tool.get("type") != "function":
            raise _InvalidResponse
        function = tool.get("function")
        if not isinstance(function, dict):
            raise _InvalidResponse
        name = function.get("name")
        parameters = function.get("parameters")
        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name) > _MAX_TOOL_NAME_CHARS
            or _has_control(name)
            or not isinstance(parameters, dict)
        ):
            raise _InvalidResponse
    return normalized


def _choice(data: object) -> tuple[str, dict[str, object]]:
    if not isinstance(data, dict):
        raise _InvalidResponse
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise _InvalidResponse
    choice = choices[0]
    finish_reason = choice.get("finish_reason")
    if not isinstance(finish_reason, str) or finish_reason not in _CHAT_FINISH_REASONS:
        raise _InvalidResponse
    message = choice.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        raise _InvalidResponse
    return finish_reason, message


def _chat_message(data: object) -> dict[str, object]:
    finish_reason, message = _choice(data)
    content = message.get("content")
    if content is None:
        content = ""
    if not isinstance(content, str):
        raise _InvalidResponse
    calls = _assistant_calls(message.get("tool_calls"), wire=False)
    if finish_reason == "tool_calls" and not calls:
        raise _InvalidResponse
    return {"role": "assistant", "content": content, "tool_calls": calls}


def _structured_content(data: object) -> object:
    finish_reason, message = _choice(data)
    if finish_reason != "stop":
        raise _InvalidResponse
    tool_calls = message.get("tool_calls")
    if tool_calls is not None and tool_calls != []:
        raise _InvalidResponse
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise _InvalidResponse
    return _strict_loads(content)
