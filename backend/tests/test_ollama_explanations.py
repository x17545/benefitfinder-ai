"""The local F13 adapter bounds requests and does not expose provider error contents."""

import json
from unittest import IsolatedAsyncioTestCase
from unittest.mock import Mock, patch

from benefitfinder.infrastructure.ollama_explanations import OllamaExplanationCompletion


class OllamaExplanationTests(IsolatedAsyncioTestCase):
    async def test_e4b_uses_structured_output_without_thinking(self) -> None:
        connection = Mock()
        connection.getresponse.return_value.status = 200
        connection.getresponse.return_value.read.return_value = json.dumps(
            {
                "done": True,
                "message": {"role": "assistant", "content": '{"details":[]}'},
            }
        ).encode()
        with patch(
            "benefitfinder.infrastructure.ollama_explanations.HTTPConnection",
            return_value=connection,
        ) as http:
            result = await OllamaExplanationCompletion("gemma4:e4b").complete(
                [], {"type": "object"}, timeout_seconds=0.5
            )
        self.assertEqual(result, {"details": []})
        self.assertEqual(http.call_args.kwargs["timeout"], 0.5)
        request = json.loads(connection.request.call_args.args[2])
        self.assertEqual(request["format"], {"type": "object"})
        self.assertIs(request["think"], False)
        self.assertIs(request["stream"], False)
        connection.close.assert_called_once()

    async def test_transport_error_and_duplicate_fields_are_fixed_failures(self) -> None:
        for raw in (None, b'{"done":true,"done":false,"message":{}}'):
            with self.subTest(raw=raw):
                connection = Mock()
                if raw is None:
                    connection.request.side_effect = OSError("virtual sensitive provider message")
                else:
                    connection.getresponse.return_value.status = 200
                    connection.getresponse.return_value.read.return_value = raw
                with (
                    patch(
                        "benefitfinder.infrastructure.ollama_explanations.HTTPConnection",
                        return_value=connection,
                    ),
                    self.assertRaisesRegex(ValueError, "^explanation_local_model_failed$"),
                ):
                    await OllamaExplanationCompletion("gemma4:e4b").complete(
                        [], {}, timeout_seconds=1
                    )
