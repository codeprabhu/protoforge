from __future__ import annotations

import json
import logging

import pytest

from agent.credentials import (
    CredentialManager,
    CredentialNotFound,
    CredentialStorageUnavailable,
    ValidationStatus,
)
from agent.providers import OllamaProvider, ProviderConfig, get_provider
from core.attempt_record import AttemptRecord


class MemoryKeyring:
    def __init__(self):
        self.values = {}

    def set_password(self, service, username, password):
        self.values[(service, username)] = password

    def get_password(self, service, username):
        return self.values.get((service, username))

    def delete_password(self, service, username):
        del self.values[(service, username)]


@pytest.fixture
def manager(tmp_path):
    backend = MemoryKeyring()
    return CredentialManager(backend=backend, profile_path=tmp_path / "profiles.json"), backend


def test_create_retrieve_update_and_delete_credential(manager):
    credentials, keyring = manager
    credentials.set_credential("OpenAI", "research", "secret-v1", display_label="Research")
    assert credentials.get_credential("openai", "research") == "secret-v1"
    credentials.set_credential("openai", "research", "secret-v2")
    assert credentials.get_credential("openai", "research") == "secret-v2"
    assert credentials.delete_credential("openai", "research") is True
    assert credentials.has_credential("openai", "research") is False
    assert keyring.values == {}


def test_profiles_isolate_providers_and_accounts(manager):
    credentials, _ = manager
    credentials.set_credential("openai", "personal", "secret-a")
    credentials.set_credential("openai", "research", "secret-b")
    credentials.set_credential("google", "personal", "secret-c")
    assert credentials.get_credential("openai", "personal") == "secret-a"
    assert credentials.get_credential("openai", "research") == "secret-b"
    assert credentials.get_credential("google", "personal") == "secret-c"
    assert [p.account for p in credentials.list_profiles("openai")] == ["personal", "research"]


def test_missing_profile_is_explicit(manager):
    with pytest.raises(CredentialNotFound):
        manager[0].get_credential("openai", "unknown")
    assert manager[0].delete_credential("openai", "unknown") is False


def test_unavailable_keyring_returns_actionable_error(monkeypatch):
    class FailingBackend:
        __module__ = "keyring.backends.fail"
        priority = 0

    class FakeKeyringModule:
        @staticmethod
        def get_keyring():
            return FailingBackend()

    monkeypatch.setitem(__import__("sys").modules, "keyring", FakeKeyringModule)
    with pytest.raises(CredentialStorageUnavailable, match="Secret Service"):
        CredentialManager().set_credential("openai", "work", "recognizable-secret")


def test_validation_results_are_sanitized_and_recorded(manager, caplog):
    credentials, _ = manager
    secret = "recognizable-secret-98765"
    credentials.set_credential("openai", "work", secret)

    def rejected(value):
        assert value == secret
        raise RuntimeError(f"Authorization: Bearer {secret}")

    caplog.set_level(logging.DEBUG)
    response = credentials.validate_credential("openai", "work", rejected)
    profile = credentials.list_profiles()[0]
    assert response["status"] == ValidationStatus.CONNECTION_FAILURE
    assert secret not in json.dumps(response)
    assert secret not in caplog.text
    assert profile.validation_status == ValidationStatus.CONNECTION_FAILURE
    assert profile.last_validated_at is not None


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [(401, "invalid_credentials"), (429, "rate_limited"), (503, "provider_unavailable")],
)
def test_validation_classifies_provider_status_without_error_text(manager, status_code, expected):
    credentials, _ = manager
    credentials.set_credential("provider", "account", "secret-never-returned")

    class ProviderError(Exception):
        pass

    error = ProviderError("secret-never-returned in request details")
    error.status_code = status_code
    result = credentials.validate_credential("provider", "account", lambda _: (_ for _ in ()).throw(error))
    assert result["status"] == expected
    assert "secret-never-returned" not in json.dumps(result)


def test_validation_without_adapter_is_unsupported(manager):
    credentials, _ = manager
    credentials.set_credential("provider", "account", "secret")
    assert credentials.validate_credential("provider", "account")["status"] == "validation_unsupported"


def test_secret_is_absent_from_profile_and_attempt_serialization(manager):
    credentials, _ = manager
    secret = "recognizable-secret-injected"
    credentials.set_credential("vendor", "lab", secret, display_label="Lab")
    metadata = (credentials._profile_path).read_text(encoding="utf-8")
    record = AttemptRecord(attempt=0, model="configured-model")
    assert secret not in metadata
    assert secret not in record.to_json()


def test_ollama_does_not_require_or_query_credentials(monkeypatch):
    class FailingBackend:
        __module__ = "keyring.backends.fail"
        priority = 0

    class FakeKeyringModule:
        @staticmethod
        def get_keyring():
            return FailingBackend()

    monkeypatch.setitem(__import__("sys").modules, "keyring", FakeKeyringModule)
    monkeypatch.setenv("PROTOFORGE_PROVIDER", "ollama")
    monkeypatch.setattr(
        "agent.ollama_client.generate_verilog",
        lambda prompt, **kwargs: {"content": "rtl"},
    )
    assert get_provider(ProviderConfig()).generate("prompt")["content"] == "rtl"
    assert OllamaProvider().metadata().local is True
