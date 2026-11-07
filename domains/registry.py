"""Domain adapter discovery and registration.

Built-in domains are loaded lazily on first lookup: the registry starts empty
and importing this module alone is enough for ``get_domain("i2c")`` to work.
Each adapter module exposes an idempotent ``register()`` so a lookup still
succeeds if the module was imported earlier and the registry was cleared.
"""

from __future__ import annotations

import importlib
from typing import Dict, List

from domains.base import BaseDomainAdapter

_REGISTRY: Dict[str, BaseDomainAdapter] = {}

# Canonical module for each built-in domain. The key doubles as the domain name
# used by Task.domain and by the error message for unknown domains.
_BUILTIN_MODULES: Dict[str, str] = {
    "i2c": "domains.i2c.adapter",
}


def register_domain(adapter: BaseDomainAdapter) -> None:
    """Register (or replace) a domain adapter instance."""
    _REGISTRY[adapter.name] = adapter


def _ensure_loaded(name: str) -> None:
    """Import the adapter module for ``name`` if it has not registered yet."""
    if name in _REGISTRY:
        return
    module_name = _BUILTIN_MODULES.get(name)
    if module_name is None:
        return
    module = importlib.import_module(module_name)
    register = getattr(module, "register", None)
    if callable(register):
        # import_module returns a cached module, so re-registration must be
        # explicit for the case where the registry was emptied after import.
        register()


def get_domain(name: str) -> BaseDomainAdapter:
    """Retrieve a registered domain adapter by name, loading it if needed."""
    _ensure_loaded(name)
    if name not in _REGISTRY:
        raise KeyError(
            f"Domain '{name}' is not registered. Available domains: {list_domains()}"
        )
    return _REGISTRY[name]


def list_domains() -> List[str]:
    """List every domain that can be resolved, loading built-ins first."""
    for builtin in _BUILTIN_MODULES:
        _ensure_loaded(builtin)
    return list(_REGISTRY.keys())
