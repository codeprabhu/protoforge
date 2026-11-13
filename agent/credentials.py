"""OS keyring backed provider credentials and non-secret profile metadata."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable

SERVICE_NAME = "protoforge"


class CredentialError(RuntimeError):
    """Base class for credential lifecycle errors with sanitized messages."""


class CredentialStorageUnavailable(CredentialError):
    """The operating system credential manager cannot be used."""


class CredentialNotFound(CredentialError):
    """No stored credential exists for the requested profile."""


class ValidationStatus(StrEnum):
    VALID = "valid"
    INVALID_CREDENTIALS = "invalid_credentials"
    CONNECTION_FAILURE = "connection_failure"
    RATE_LIMITED = "rate_limited"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    VALIDATION_UNSUPPORTED = "validation_unsupported"
    SECURE_STORAGE_UNAVAILABLE = "secure_storage_unavailable"


@dataclass(frozen=True)
class CredentialProfile:
    provider: str
    account: str
    display_label: str | None = None
    created_at: float = 0
    last_validated_at: float | None = None
    validation_status: str | None = None


def _default_profile_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.getenv("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "protoforge" / "credential-profiles.json"


def _profile_key(provider: str, account: str) -> str:
    return f"{provider.strip().lower()}:{account.strip()}"


class CredentialManager:
    """Store secret values in the OS keyring; keep only profile metadata locally."""

    def __init__(self, *, backend: Any | None = None, profile_path: str | Path | None = None):
        self._backend = backend
        self._profile_path = Path(profile_path) if profile_path else _default_profile_path()

    def _keyring(self):
        if self._backend is not None:
            return self._backend
        try:
            import keyring

            backend = keyring.get_keyring()
        except Exception:
            raise CredentialStorageUnavailable(
                "Secure credential storage is unavailable. Install and unlock an OS keyring (Linux: Secret Service)."
            ) from None

        module = type(backend).__module__.lower()
        if "fail" in module or "keyrings.alt" in module or getattr(backend, "priority", 0) <= 0:
            raise CredentialStorageUnavailable(
                "No secure OS credential store is available. Configure Secret Service or another supported system keyring."
            ) from None
        if sys.platform.startswith("linux") and "secretservice" not in module:
            raise CredentialStorageUnavailable(
                "Linux credential storage requires an active Secret Service keyring backend."
            )
        return backend

    def _read_profiles(self) -> dict[str, dict[str, Any]]:
        try:
            return json.loads(self._profile_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError):
            raise CredentialError("Credential profile metadata could not be read.") from None

    def _write_profiles(self, profiles: dict[str, dict[str, Any]]) -> None:
        try:
            self._profile_path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(
                prefix="credential-profiles-", suffix=".tmp", dir=self._profile_path.parent
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump(profiles, stream, indent=2, sort_keys=True)
                    stream.write("\n")
                os.replace(temporary, self._profile_path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        except OSError:
            raise CredentialError("Credential profile metadata could not be saved.") from None

    def set_credential(
        self, provider: str, account: str, secret: str, *, display_label: str | None = None
    ) -> CredentialProfile:
        provider = provider.strip().lower()
        account = account.strip()
        if not provider or not account or not isinstance(secret, str) or not secret:
            raise ValueError("Provider, account, and a non-empty credential are required.")
        key = _profile_key(provider, account)
        profiles = self._read_profiles()
        previous = profiles.get(key)
        created_at = previous["created_at"] if previous else time.time()
        profile = CredentialProfile(
            provider=provider,
            account=account,
            display_label=display_label if display_label is not None else (previous or {}).get("display_label"),
            created_at=created_at,
            last_validated_at=(previous or {}).get("last_validated_at"),
            validation_status=(previous or {}).get("validation_status"),
        )
        try:
            self._keyring().set_password(SERVICE_NAME, key, secret)
        except CredentialStorageUnavailable:
            raise
        except Exception:
            raise CredentialStorageUnavailable("The OS credential store could not save this profile.") from None
        profiles[key] = asdict(profile)
        self._write_profiles(profiles)
        return profile

    def get_credential(self, provider: str, account: str) -> str:
        key = _profile_key(provider, account)
        try:
            secret = self._keyring().get_password(SERVICE_NAME, key)
        except CredentialStorageUnavailable:
            raise
        except Exception:
            raise CredentialStorageUnavailable("The OS credential store could not retrieve this profile.") from None
        if secret is None:
            raise CredentialNotFound(f"No credential is configured for {key}.")
        return secret

    def has_credential(self, provider: str, account: str) -> bool:
        try:
            self.get_credential(provider, account)
            return True
        except CredentialNotFound:
            return False

    def delete_credential(self, provider: str, account: str) -> bool:
        key = _profile_key(provider, account)
        profiles = self._read_profiles()
        if key not in profiles:
            return False
        try:
            self._keyring().delete_password(SERVICE_NAME, key)
        except CredentialStorageUnavailable:
            raise
        except Exception:
            raise CredentialStorageUnavailable("The OS credential store could not delete this profile.") from None
        profiles.pop(key, None)
        self._write_profiles(profiles)
        return True

    def list_profiles(self, provider: str | None = None) -> list[CredentialProfile]:
        profiles = self._read_profiles().values()
        selected = provider.strip().lower() if provider else None
        return [
            CredentialProfile(**profile)
            for profile in profiles
            if selected is None or profile["provider"] == selected
        ]

    def validate_credential(
        self,
        provider: str,
        account: str,
        validator: Callable[[str], Any] | None = None,
    ) -> dict[str, str]:
        """Validate through a provider callback and return a sanitized status."""
        if validator is None:
            try:
                self.get_credential(provider, account)
            except CredentialStorageUnavailable:
                return {"status": ValidationStatus.SECURE_STORAGE_UNAVAILABLE, "message": "Secure credential storage is unavailable."}
            except CredentialNotFound:
                return {"status": ValidationStatus.INVALID_CREDENTIALS, "message": "No credential is configured for this profile."}
            return self._record_validation(provider, account, ValidationStatus.VALIDATION_UNSUPPORTED)
        try:
            secret = self.get_credential(provider, account)
        except CredentialStorageUnavailable:
            return {"status": ValidationStatus.SECURE_STORAGE_UNAVAILABLE, "message": "Secure credential storage is unavailable."}
        except CredentialNotFound:
            return {"status": ValidationStatus.INVALID_CREDENTIALS, "message": "No credential is configured for this profile."}

        try:
            result = validator(secret)
            status = ValidationStatus.VALID if result is not False else ValidationStatus.INVALID_CREDENTIALS
        except Exception as error:
            status = _validation_status(error)
        return self._record_validation(provider, account, status)

    def _record_validation(self, provider: str, account: str, status: ValidationStatus) -> dict[str, str]:
        key = _profile_key(provider, account)
        profiles = self._read_profiles()
        if key in profiles:
            profiles[key]["last_validated_at"] = time.time()
            profiles[key]["validation_status"] = status.value
            self._write_profiles(profiles)
        return {"status": status.value, "message": _validation_message(status)}


def _validation_status(error: Exception) -> ValidationStatus:
    code = getattr(error, "status_code", None) or getattr(error, "status", None)
    if code == 401 or code == 403:
        return ValidationStatus.INVALID_CREDENTIALS
    if code == 429:
        return ValidationStatus.RATE_LIMITED
    if code in {500, 502, 503, 504}:
        return ValidationStatus.PROVIDER_UNAVAILABLE
    if isinstance(error, (TimeoutError, ConnectionError, OSError)):
        return ValidationStatus.CONNECTION_FAILURE
    return ValidationStatus.CONNECTION_FAILURE


def _validation_message(status: ValidationStatus) -> str:
    return {
        ValidationStatus.VALID: "Credential validated successfully.",
        ValidationStatus.INVALID_CREDENTIALS: "The provider rejected this credential.",
        ValidationStatus.CONNECTION_FAILURE: "Could not connect to the provider.",
        ValidationStatus.RATE_LIMITED: "The provider rate limited the validation request.",
        ValidationStatus.PROVIDER_UNAVAILABLE: "The provider is temporarily unavailable.",
        ValidationStatus.VALIDATION_UNSUPPORTED: "Credential validation is not supported for this provider yet.",
        ValidationStatus.SECURE_STORAGE_UNAVAILABLE: "Secure credential storage is unavailable.",
    }[status]
