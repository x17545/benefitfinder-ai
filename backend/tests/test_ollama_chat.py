import json
from unittest import TestCase
from unittest.mock import patch

from benefitfinder.infrastructure.ollama_chat import OllamaChat


class OllamaChatTests(TestCase):
    def test_thinking_override_is_sent_only_to_e4b(self) -> None:
        for model in ("gemma4:e4b", "gemma4:e2b", "qwen3:8b"):
            with (
                self.subTest(model=model),
                patch("benefitfinder.infrastructure.ollama_chat.HTTPConnection") as http,
            ):
                connection = http.return_value
                response = connection.getresponse.return_value
                response.status = 200
                response.read.return_value = b'{"message":{"role":"assistant","content":""}}'

                OllamaChat(model)([{"role": "user", "content": "test"}], [])

                method, path, body, _ = connection.request.call_args.args
                self.assertEqual((method, path), ("POST", "/api/chat"))
                request = json.loads(body)
                if model == "gemma4:e4b":
                    self.assertIs(request["think"], False)
                else:
                    self.assertNotIn("think", request)
