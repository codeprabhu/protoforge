"""Model provider contract and currently supported provider adapters."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from agent import ollama_client


@dataclass(frozen=True)
class ModelMetadata:
    provider: str
    model: str
    local: bool


@dataclass(frozen=True)
class ProviderConfig:
    """Non-secret provider selection and credential-profile reference."""

    provider: str = "ollama"
    model: str | None = None
    account: str | None = None
    endpoint: str | None = None


@runtime_checkable
class ModelProvider(Protocol):
    """Common synchronous generation contract used by the RTL pipeline."""

    name: str

    def metadata(self, model: str) -> ModelMetadata: ...

    def generate(
        self,
        prompt: str,
        *,
        model: str | None = None,
        temperature: float | None = None,
        seed: int | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]: ...


class OllamaProvider:
    name = "ollama"

    def __init__(self, config: ProviderConfig | None = None):
        self.config = config or ProviderConfig()

    def metadata(self, model: str | None = None) -> ModelMetadata:
        return ModelMetadata(
            provider=self.name,
            model=model or self.config.model or ollama_client.DEFAULT_MODEL,
            local=True,
        )

    def generate(
        self,
        prompt: str,
        *,
        model: str | None = None,
        temperature: float | None = None,
        seed: int | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        result = ollama_client.generate_verilog(
            prompt,
            model=model or self.config.model or ollama_client.DEFAULT_MODEL,
            temperature=temperature,
            seed=ollama_client.DEFAULT_SEED if seed is None else seed,
            with_metadata=True,
            timeout=timeout,
            host=self.config.endpoint,
        )
        if isinstance(result, str):
            result = {"content": result}
        return {
            **result,
            "provider": self.name,
            "provider_model": result.get("model") or model or self.config.model or ollama_client.DEFAULT_MODEL,
            "local": True,
        }


def get_provider(name: str | ProviderConfig | None = None) -> ModelProvider:
    """Resolve a provider from non-secret configuration or PROTOFORGE_PROVIDER."""
    config = name if isinstance(name, ProviderConfig) else None
    configured_name = config.provider if config else name
    selected = (configured_name or os.getenv("PROTOFORGE_PROVIDER", "ollama")).strip().lower()
    if selected == "ollama":
        return OllamaProvider(config)
    raise ValueError(f"Unsupported model provider: {selected}")
