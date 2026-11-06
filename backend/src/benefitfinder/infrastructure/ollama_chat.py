"""Call an installed Ollama model over the local chat API."""

from __future__ import annotations

import json
import re
from http.client import HTTPConnection, HTTPException

_FAILURE = "agent_local_model_failed"
_MAX_BYTES = 1_000_000


class OllamaChat:
    def __init__(self, model: str) -> None:
        if (
            not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}", model)
            or "cloud" in model.lower()
        ):
            raise ValueError("agent_local_model_name_required")
        self._model = model

    def __call__(
        self, messages: list[dict[str, object]], tools: list[dict[str, object]]
    ) -> dict[str, object]:
        body = json.dumps(
            {
                "model": self._model,
                "messages": messages,
                "tools": tools,
                "stream": False,
                "options": {"temperature": 0},
            },
            ensure_ascii=False,
        ).encode("utf-8")
        connection = HTTPConnection("127.0.0.1", 11434, timeout=60)
        try:
            connection.request("POST", "/api/chat", body, {"Content-Type": "application/json"})
            response = connection.getresponse()
            if response.status != 200:
                raise ValueError(_FAILURE)
            raw = response.read(_MAX_BYTES + 1)
            if len(raw) > _MAX_BYTES:
                raise ValueError(_FAILURE)
            data = json.loads(raw)
        except (HTTPException, OSError, ValueError):
            raise ValueError(_FAILURE) from None
        finally:
            connection.close()
        message = data.get("message") if isinstance(data, dict) else None
        if not isinstance(message, dict) or message.get("role") != "assistant":
            raise ValueError(_FAILURE)
        content = message.get("content")
        if not isinstance(content, str):
            raise ValueError(_FAILURE)
        return {"role": "assistant", "content": content, "tool_calls": message.get("tool_calls")}
