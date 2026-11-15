
"""Regression tests for the ASGI entrypoint's LLM and database settings."""

from __future__ import annotations

import os
import runpy
import unittest
from pathlib import Path
from unittest.mock import patch

from benefitfinder.ai.policy_explanation import GroundedPolicyExplainer


MAIN_FILE = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "benefitfinder"
    / "main.py"
)


class MainLLMConfigurationTests(unittest.TestCase):
    def check_configuration(
        self,
        environment: dict[str, str],
        expected_model: str | None,
        expect_generator: bool,
    ) -> None:
        values = {
            "DATABASE_URL": "postgresql:///fictional_clauses_test",
            "POLICY_DATABASE_URL": "postgresql:///fictional_policies_test",
            **environment,
        }

        with (
            patch.dict(os.environ, values, clear=True),
            patch("benefitfinder.bootstrap.create_app") as create_app,
        ):
            runpy.run_path(str(MAIN_FILE), run_name="benefitfinder_main_test")

        create_app.assert_called_once()
        kwargs = create_app.call_args.kwargs

        self.assertEqual(kwargs["explanation_model"], expected_model)
        self.assertEqual(
            kwargs["explanation_database_url"],
            "postgresql:///fictional_clauses_test",
        )
        self.assertEqual(
            kwargs["policy_database_url"],
            "postgresql:///fictional_policies_test",
        )

        generator = kwargs["explanation_generator"]
        if expect_generator:
            self.assertIsInstance(generator, GroundedPolicyExplainer)
        else:
            self.assertIsNone(generator)

    def test_no_provider_uses_default_fallback(self) -> None:
        self.check_configuration(
            environment={},
            expected_model=None,
            expect_generator=False,
        )

    def test_disabled_provider_preserves_ollama(self) -> None:
        self.check_configuration(
            environment={
                "LLM_PROVIDER": "disabled",
                "F13_OLLAMA_MODEL": "gemma4:e4b",
            },
            expected_model="gemma4:e4b",
            expect_generator=False,
        )

    def test_llama_cpp_takes_priority_over_ollama(self) -> None:
        self.check_configuration(
            environment={
                "LLM_PROVIDER": "llama_cpp",
                "F13_OLLAMA_MODEL": "gemma4:e4b",
            },
            expected_model=None,
            expect_generator=True,
        )

    def test_external_api_takes_priority_over_ollama(self) -> None:
        self.check_configuration(
            environment={
                "LLM_PROVIDER": "external_api",
                "LLM_BASE_URL": "https://api.example/v1",
                "LLM_MODEL": "fictional-model",
                "LLM_API_KEY": "fictional-test-key",
                "F13_OLLAMA_MODEL": "gemma4:e4b",
            },
            expected_model=None,
            expect_generator=True,
        )


if __name__ == "__main__":
    unittest.main()
