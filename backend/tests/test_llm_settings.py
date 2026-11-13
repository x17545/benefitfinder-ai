"""Provider configuration, secret-file handling and bootstrap wiring."""

from __future__ import annotations

import io
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from benefitfinder.infrastructure.llm_settings import LLMSettings


class LLMSettingsTest(unittest.TestCase):
    def test_default_is_disabled_and_local_mode_has_loopback_defaults(self):
        self.assertEqual(LLMSettings.from_env({}).provider, "disabled")
        settings = LLMSettings.from_env({"LLM_PROVIDER": "llama_cpp"})
        self.assertEqual(settings.base_url, "http://127.0.0.1:18080/v1")
        self.assertEqual(settings.model, "gemma4-e4b")

    def test_explicit_external_settings_and_secrets_are_not_repr(self):
        values = {
            "LLM_PROVIDER": "external_api",
            "LLM_BASE_URL": "https://api.example/v1",
            "LLM_MODEL": "vendor/test-model",
            "LLM_API_KEY": "fictional-secret",
        }
        settings = LLMSettings.from_env(values)
        self.assertNotIn("fictional-secret", repr(settings))
        self.assertNotIn("fictional-secret", repr(settings.endpoint()))
        for removed in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"):
            with self.subTest(removed=removed), self.assertRaises(ValueError):
                LLMSettings.from_env(
                    {key: value for key, value in values.items() if key != removed}
                )

    def test_key_file_conflict_missing_and_oversized_are_safe(self):
        with TemporaryDirectory() as directory:
            key = Path(directory) / "key"
            key.write_text("fictional-key-from-file\n")
            values = {"LLM_PROVIDER": "llama_cpp", "LLM_API_KEY_FILE": str(key)}
            settings = LLMSettings.from_env(values)
            self.assertEqual(settings.api_key, "fictional-key-from-file")
            self.assertNotIn("fictional-key-from-file", repr(settings))
            with self.assertRaisesRegex(ValueError, "ambiguous_llm_api_key"):
                LLMSettings.from_env({**values, "LLM_API_KEY": "fictional-second"})
            key.write_text("x" * 5000)
            with self.assertRaisesRegex(ValueError, "invalid_llm_api_key_file"):
                LLMSettings.from_env(values)
            key.unlink()
            with self.assertRaisesRegex(ValueError, "invalid_llm_api_key_file"):
                LLMSettings.from_env(values)

    def test_local_provider_rejects_external_destination(self):
        with self.assertRaises(ValueError):
            LLMSettings.from_env(
                {"LLM_PROVIDER": "llama_cpp", "LLM_BASE_URL": "https://api.example/v1"}
            )

    def test_extraction_uses_configured_provider_without_ollama(self):
        from benefitfinder.jobs.__main__ import extraction_chat

        with (
            patch.dict(os.environ, {"LLM_PROVIDER": "llama_cpp"}, clear=True),
            patch(
                "benefitfinder.jobs.__main__.CompatibleChat",
                return_value=object(),
            ) as compatible,
            patch("benefitfinder.jobs.__main__.OllamaChat") as ollama,
        ):
            extraction_chat(None)
        compatible.assert_called_once()
        ollama.assert_not_called()

    def test_explanation_example_uses_configured_provider(self):
        from benefitfinder.jobs.__main__ import main

        with (
            patch.dict(os.environ, {"LLM_PROVIDER": "llama_cpp"}, clear=True),
            patch(
                "benefitfinder.jobs.__main__.CompatibleExplanationCompletion",
            ) as completion,
            patch("benefitfinder.jobs.__main__.asyncio.run") as run,
            patch(
                "benefitfinder.jobs.__main__.asdict",
                return_value={},
            ),
            patch("sys.stdout", new=io.StringIO()),
        ):
            # Close the coroutine rather than execute a real model in this unit test.
            run.side_effect = lambda coroutine: coroutine.close()
            self.assertEqual(main(["explain-example"]), 0)
        completion.assert_called_once()


if __name__ == "__main__":
    unittest.main()
