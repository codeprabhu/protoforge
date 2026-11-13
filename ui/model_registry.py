"""Persistent, non-secret provider and model configuration."""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


class RegistryError(ValueError):
    pass


def default_registry_path() -> Path:
    root = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "protoforge" / "model-registry.json"


class ModelRegistry:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else default_registry_path()

    def _load(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"providers": {}, "models": {}, "default_model": None}
        except (OSError, json.JSONDecodeError):
            raise RegistryError("Model registry data could not be read.") from None

    def _save(self, data: dict[str, Any]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(
                prefix="model-registry-", suffix=".tmp", dir=self.path.parent
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump(data, stream, indent=2, sort_keys=True)
                    stream.write("\n")
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        except OSError:
            raise RegistryError("Model registry data could not be saved.") from None

    @staticmethod
    def _identifier(value: str, label: str) -> str:
        if not isinstance(value, str):
            raise RegistryError(f"{label} must be text.")
        value = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,79}", value):
            raise RegistryError(f"{label} must use letters, numbers, '.', '_' or '-'.")
        return value

    def list_providers(self) -> list[dict[str, Any]]:
        return list(self._load()["providers"].values())

    def get_provider(self, provider_id: str) -> dict[str, Any] | None:
        return self._load()["providers"].get(provider_id)

    def register_provider(self, payload: dict[str, Any]) -> dict[str, Any]:
        provider_id = self._identifier(payload.get("provider_id", ""), "Provider identifier")
        provider_type = self._identifier(payload.get("provider_type") or provider_id, "Provider type")
        display_name = _required_text(payload.get("display_name"), "Display name")
        endpoint = _endpoint(payload.get("endpoint_url"))
        account = _optional_text(payload.get("credential_account"))
        data = self._load()
        if provider_id in data["providers"]:
            raise RegistryError("Provider is already registered.")
        record = {
            "provider_id": provider_id,
            "provider_type": provider_type,
            "display_name": display_name,
            "endpoint_url": endpoint,
            "credential_account": account,
            "status": "configured",
            "last_checked_at": None,
            "last_error": None,
            "available_models": [],
            "created_at": time.time(),
        }
        data["providers"][provider_id] = record
        self._save(data)
        return record

    def update_provider(self, provider_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = self._load()
        record = data["providers"].get(provider_id)
        if record is None:
            raise KeyError("Provider not found.")
        if "display_name" in payload:
            record["display_name"] = _required_text(payload["display_name"], "Display name")
        if "provider_type" in payload:
            record["provider_type"] = self._identifier(payload["provider_type"], "Provider type")
        if "endpoint_url" in payload:
            record["endpoint_url"] = _endpoint(payload["endpoint_url"])
        if "credential_account" in payload:
            record["credential_account"] = _optional_text(payload["credential_account"])
        self._save(data)
        return record

    def remove_provider(self, provider_id: str) -> bool:
        data = self._load()
        if provider_id not in data["providers"]:
            return False
        if any(model["provider_id"] == provider_id for model in data["models"].values()):
            raise RegistryError("Remove this provider's model entries before removing the provider.")
        del data["providers"][provider_id]
        self._save(data)
        return True

    def list_models(self, provider_id: str | None = None) -> list[dict[str, Any]]:
        values = self._load()["models"].values()
        return [m for m in values if provider_id is None or m["provider_id"] == provider_id]

    def register_model(self, payload: dict[str, Any]) -> dict[str, Any]:
        provider_id = self._identifier(payload.get("provider_id", ""), "Provider identifier")
        model_identifier = _required_text(payload.get("model_identifier"), "Model identifier")
        display_name = _required_text(payload.get("display_name"), "Display name")
        data = self._load()
        if provider_id not in data["providers"]:
            raise RegistryError("Register the provider before adding its models.")
        if any(m["provider_id"] == provider_id and m["model_identifier"] == model_identifier
               for m in data["models"].values()):
            raise RegistryError("Model is already registered for this provider.")
        local = payload.get("local", provider_id == "ollama")
        enabled = payload.get("enabled", True)
        if not isinstance(local, bool) or not isinstance(enabled, bool):
            raise RegistryError("Local designation must be true or false.")
        context_window = payload.get("context_window")
        if context_window is not None and (not isinstance(context_window, int) or isinstance(context_window, bool) or context_window <= 0):
            raise RegistryError("Context window must be a positive integer or unknown.")
        temperature = payload.get("default_temperature", 0.0)
        timeout = payload.get("generation_timeout", 120.0)
        if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not math.isfinite(temperature) or not 0 <= temperature <= 2:
            raise RegistryError("Default temperature must be between 0 and 2.")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise RegistryError("Generation timeout must be positive.")
        capabilities = payload.get("capabilities")
        if capabilities is not None and (not isinstance(capabilities, list) or not all(isinstance(x, str) for x in capabilities)):
            raise RegistryError("Capabilities must be a list of names or unknown.")
        model_id = str(uuid.uuid4())
        record = {
            "model_id": model_id,
            "provider_id": provider_id,
            "model_identifier": model_identifier,
            "display_name": display_name,
            "local": local,
            "context_window": context_window,
            "capabilities": capabilities,
            "default_temperature": float(temperature),
            "generation_timeout": float(timeout),
            "enabled": enabled,
            "availability": "unknown",
        }
        data["models"][model_id] = record
        self._save(data)
        return record

    def update_model(self, model_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = self._load()
        model = data["models"].get(model_id)
        if model is None:
            raise KeyError("Model not found.")
        allowed = {"display_name", "model_identifier", "context_window", "capabilities", "default_temperature", "generation_timeout", "enabled", "local"}
        if set(payload) - allowed:
            raise RegistryError("Model update contains unsupported fields.")
        proposed = {**model, **payload}
        if "model_identifier" in payload:
            proposed["model_identifier"] = _required_text(payload["model_identifier"], "Model identifier")
            if any(
                other_id != model_id
                and other["provider_id"] == model["provider_id"]
                and other["model_identifier"] == proposed["model_identifier"]
                for other_id, other in data["models"].items()
            ):
                raise RegistryError("Model is already registered for this provider.")
        if "display_name" in payload:
            proposed["display_name"] = _required_text(payload["display_name"], "Display name")
        if proposed["context_window"] is not None and (
            not isinstance(proposed["context_window"], int) or isinstance(proposed["context_window"], bool) or proposed["context_window"] <= 0
        ):
            raise RegistryError("Context window must be a positive integer or unknown.")
        if proposed["capabilities"] is not None and (
            not isinstance(proposed["capabilities"], list)
            or not all(isinstance(item, str) for item in proposed["capabilities"])
        ):
            raise RegistryError("Capabilities must be a list of names or unknown.")
        temperature = proposed["default_temperature"]
        if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not math.isfinite(temperature) or not 0 <= temperature <= 2:
            raise RegistryError("Default temperature must be between 0 and 2.")
        timeout = proposed["generation_timeout"]
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise RegistryError("Generation timeout must be positive.")
        if not isinstance(proposed["enabled"], bool) or not isinstance(proposed["local"], bool):
            raise RegistryError("Enabled and local settings must be true or false.")
        if data["default_model"] == model_id and not proposed["enabled"]:
            raise RegistryError("The default model cannot be disabled before changing the default.")
        data["models"][model_id] = proposed
        self._save(data)
        return proposed

    def remove_model(self, model_id: str) -> bool:
        data = self._load()
        if model_id not in data["models"]:
            return False
        del data["models"][model_id]
        if data["default_model"] == model_id:
            data["default_model"] = None
        self._save(data)
        return True

    def set_default_model(self, model_id: str) -> dict[str, Any]:
        data = self._load()
        model = data["models"].get(model_id)
        if model is None:
            raise KeyError("Model not found.")
        if not model["enabled"]:
            raise RegistryError("Disabled models cannot be selected as the default.")
        data["default_model"] = model_id
        self._save(data)
        return model

    def default_model(self) -> str | None:
        return self._load()["default_model"]

    def record_provider_status(self, provider_id: str, status: str, *, error: str | None = None,
                               available_models: list[str] | None = None) -> None:
        data = self._load()
        provider = data["providers"].get(provider_id)
        if provider is None:
            raise KeyError("Provider not found.")
        provider["status"] = status
        provider["last_checked_at"] = time.time()
        provider["last_error"] = error
        if available_models is not None:
            provider["available_models"] = available_models
            for model in data["models"].values():
                if model["provider_id"] == provider_id:
                    model["availability"] = "available" if model["model_identifier"] in available_models else "unavailable"
        self._save(data)


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RegistryError(f"{label} is required.")
    return value.strip()


def _optional_text(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise RegistryError("Credential profile must be a text account name.")
    return value.strip() or None


def _endpoint(value: Any) -> str | None:
    value = _optional_text(value)
    if value is None:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise RegistryError("Endpoint must be an HTTP(S) URL without embedded credentials.")
    return value.rstrip("/")
