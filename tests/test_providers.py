from __future__ import annotations

import pytest

from agent import ollama_client
from agent.providers import ModelProvider, OllamaProvider, get_provider
from agent import rtl_generator


def test_ollama_provider_implements_contract_and_exposes_metadata(monkeypatch):
    calls = []

    def fake_generate(prompt, **kwargs):
        calls.append((prompt, kwargs))
        return {"content": "module top; endmodule", "eval_count": 9, "model": "local:v1"}

    monkeypatch.setattr(ollama_client, "generate_verilog", fake_generate)
    provider = OllamaProvider()

    assert isinstance(provider, ModelProvider)
    assert provider.metadata("local:v1").provider == "ollama"
    assert provider.metadata("local:v1").local is True
    response = provider.generate("prompt", model="local:v1", seed=23)

    assert response["content"] == "module top; endmodule"
    assert response["eval_count"] == 9
    assert response["provider"] == "ollama"
    assert response["provider_model"] == "local:v1"
    assert calls[0][1]["seed"] == 23
    assert calls[0][1]["with_metadata"] is True


def test_ollama_client_passes_timeout_to_client(monkeypatch):
    calls = {}

    class FakeClient:
        def __init__(self, *, timeout):
            calls["timeout"] = timeout

        def chat(self, **kwargs):
            calls["chat"] = kwargs
            return {"message": {"content": "rtl"}, "eval_count": 2}

    class FakeOllama:
        Client = FakeClient

    monkeypatch.setattr(ollama_client, "_require_ollama", lambda: FakeOllama)
    response = ollama_client.generate_verilog_with_metadata("prompt", timeout=3.5)

    assert calls["timeout"] == 3.5
    assert response["content"] == "rtl"
    assert response["eval_count"] == 2


def test_ollama_provider_propagates_provider_errors(monkeypatch):
    def fail(*args, **kwargs):
        raise TimeoutError("generation timed out")

    monkeypatch.setattr(ollama_client, "generate_verilog", fail)
    with pytest.raises(TimeoutError, match="generation timed out"):
        OllamaProvider().generate("prompt", timeout=1)


def test_provider_selection_defaults_to_ollama_and_rejects_unknown(monkeypatch):
    monkeypatch.delenv("PROTOFORGE_PROVIDER", raising=False)
    assert isinstance(get_provider(), OllamaProvider)
    monkeypatch.setenv("PROTOFORGE_PROVIDER", "not-installed")
    with pytest.raises(ValueError, match="Unsupported model provider"):
        get_provider()


def test_rtl_generator_uses_selected_provider(monkeypatch):
    monkeypatch.setenv("PROTOFORGE_PROVIDER", "ollama")
    monkeypatch.setattr(
        ollama_client,
        "generate_verilog",
        lambda prompt, **kwargs: {"content": "selected provider", "provider": "ollama"},
    )

    assert rtl_generator.generate_verilog("prompt", with_metadata=True)["content"] == "selected provider"
