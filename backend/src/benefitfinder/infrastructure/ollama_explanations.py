"""Bounded structured completion against a local Ollama model."""

import asyncio
import json
import re
from http.client import HTTPConnection, HTTPException


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("invalid_completion_json")
        result[key] = value
    return result


class OllamaExplanationCompletion:
    def __init__(self, model: str) -> None:
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", model)
            or "cloud" in model.casefold()
        ):
            raise ValueError("local_explanation_model_required")
        self._model = model

    async def complete(
        self, messages: list[dict[str, str]], schema: dict[str, object], *, timeout_seconds: float
    ) -> object:
        return await asyncio.to_thread(self._complete, messages, schema, timeout_seconds)

    def _complete(
        self, messages: list[dict[str, str]], schema: dict[str, object], timeout_seconds: float
    ) -> object:
        payload: dict[str, object] = {
            "model": self._model,
            "messages": messages,
            "format": schema,
            "stream": False,
            "options": {"temperature": 0, "num_predict": 512},
        }
        if self._model.casefold() == "gemma4:e4b":
            payload["think"] = False
        connection = HTTPConnection("127.0.0.1", 11434, timeout=min(5, timeout_seconds))
        try:
            connection.request(
                "POST",
                "/api/chat",
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                {"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            raw = response.read(128001)
            if response.status != 200 or len(raw) > 128000:
                raise ValueError
            result = json.loads(raw, object_pairs_hook=_unique_object)
            if not isinstance(result, dict) or result.get("done") is not True:
                raise ValueError
            message = result.get("message")
            if not isinstance(message, dict) or message.get("role") != "assistant":
                raise ValueError
            content = message.get("content")
            if not isinstance(content, str):
                raise ValueError
            return json.loads(content, object_pairs_hook=_unique_object)
        except (OSError, HTTPException, ValueError):
            raise ValueError("explanation_local_model_failed") from None
        finally:
            connection.close()
