"""The synthetic F13 command is runnable without any API key or local model."""

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from unittest import TestCase
from unittest.mock import patch

from benefitfinder.jobs.__main__ import main


class ExplanationCommandTests(TestCase):
    def test_no_model_uses_virtual_template_without_opening_a_model_connection(self):
        output = io.StringIO()
        with (
            patch("benefitfinder.jobs.__main__.OllamaExplanationCompletion") as completion,
            redirect_stdout(output),
        ):
            status = main(["explain-example", "--category", "housing"])
        result = json.loads(output.getvalue())
        self.assertEqual(status, 0)
        completion.assert_not_called()
        self.assertIs(result["virtual_data"], True)
        self.assertIs(result["ai_generated"], False)
        self.assertEqual(result["ai_sources"], [])
        self.assertEqual(result["fallback_reason"], "model_unconfigured")

    def test_invalid_model_is_a_fixed_cli_error(self):
        output = io.StringIO()
        with redirect_stderr(output):
            status = main(["explain-example", "--model", "https://example.invalid/private"])
        self.assertEqual(status, 2)
        self.assertEqual(
            output.getvalue(), "설명 예제 실행 실패: local_explanation_model_required\n"
        )
