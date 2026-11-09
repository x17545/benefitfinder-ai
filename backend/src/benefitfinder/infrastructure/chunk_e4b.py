"""F12 문맥 분할용 로컬 E4B 채팅 어댑터.

표준 라이브러리로만 루프백 Ollama의 /api/chat을 호출한다.
실제 원문이 담긴 메시지나 모델 응답을 오류에 포함하지 않는다.
"""

from __future__ import annotations

import http.client
import json

from benefitfinder.application.source_chunks import evidence_schema

_HOST = "127.0.0.1"
_PORT = 11434
_PATH = "/api/chat"
_MODEL = "gemma4:e4b"
_TIMEOUT_SECONDS = 60
_MAX_RESPONSE_BYTES = 1024 * 1024


class LocalE4BChat:
    """루프백 Ollama에 JSON 모드로 요청해 assistant 본문을 돌려준다."""

    def __call__(
        self, messages: list[dict[str, str]], first_line: int, last_line: int
    ) -> str:
        try:
            payload = {
                "model": _MODEL,
                "messages": messages,
                "think": False,
                "format": evidence_schema(first_line, last_line),
                "stream": False,
                "options": {
                    "temperature": 0,
                    "num_ctx": 16384,
                    "num_predict": 4096,
                },
            }
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            conn = http.client.HTTPConnection(_HOST, _PORT, timeout=_TIMEOUT_SECONDS)
            try:
                conn.request(
                    "POST",
                    _PATH,
                    body=body,
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                )
                response = conn.getresponse()
                if response.status != 200:
                    raise ValueError("chunk_model_failed")
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
                if raw is None or len(raw) > _MAX_RESPONSE_BYTES:
                    raise ValueError("chunk_model_failed")
                try:
                    data = json.loads(raw.decode("utf-8"))
                except ValueError:
                    raise ValueError("chunk_model_failed") from None
                if not isinstance(data, dict):
                    raise ValueError("chunk_model_failed")
                if data.get("done") is not True:
                    raise ValueError("chunk_model_failed")
                if data.get("done_reason") != "stop":
                    raise ValueError("chunk_model_failed")
                message = data.get("message")
                if not isinstance(message, dict):
                    raise ValueError("chunk_model_failed")
                if message.get("role") != "assistant":
                    raise ValueError("chunk_model_failed")
                content = message.get("content")
                if not isinstance(content, str) or not content.strip():
                    raise ValueError("chunk_model_failed")
                return content
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
        except Exception:
            raise ValueError("chunk_model_failed") from None
