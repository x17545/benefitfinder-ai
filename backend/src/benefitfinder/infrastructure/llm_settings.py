"""Explicit local-development and external-production LLM configuration."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from benefitfinder.infrastructure.compatible_llm import LLMEndpoint


@dataclass(frozen=True)
class LLMSettings:
    provider: str = "disabled"
    base_url: str = ""
    model: str = ""
    api_key: str | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> LLMSettings:
        values = os.environ if environ is None else environ
        provider = values.get("LLM_PROVIDER", "disabled")
        if provider not in {"disabled", "llama_cpp", "external_api"}:
            raise ValueError("invalid_llm_provider")
        if provider == "disabled":
            return cls()
        key = values.get("LLM_API_KEY") or None
        key_file = values.get("LLM_API_KEY_FILE") or None
        if key and key_file:
            raise ValueError("ambiguous_llm_api_key")
        if key_file:
            try:
                with Path(key_file).open("r", encoding="utf-8") as source:
                    key = source.read(4097).strip()
                if not key or len(key) > 4096:
                    raise ValueError
            except (OSError, UnicodeError, ValueError):
                raise ValueError("invalid_llm_api_key_file") from None
        local = provider == "llama_cpp"
        settings = cls(
            provider,
            values.get("LLM_BASE_URL", "http://127.0.0.1:18080/v1" if local else ""),
            values.get("LLM_MODEL", "gemma4-e4b" if local else ""),
            key,
        )
        settings.endpoint()
        return settings

    def endpoint(self, *, model: str | None = None) -> LLMEndpoint:
        if self.provider == "disabled":
            raise ValueError("llm_not_configured")
        return LLMEndpoint(
            self.base_url, model or self.model, self.api_key, local=self.provider == "llama_cpp"
        )
