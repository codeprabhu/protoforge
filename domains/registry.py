"""Domain adapter discovery and registration."""

from __future__ import annotations

from typing import Dict, List, Optional
from domains.base import BaseDomainAdapter

_REGISTRY: Dict[str, BaseDomainAdapter] = {}


def register_domain(adapter: BaseDomainAdapter) -> None:
    """Register a domain adapter instance."""
    _REGISTRY[adapter.name] = adapter


def get_domain(name: str) -> BaseDomainAdapter:
    """Retrieve a registered domain adapter by name."""
    if name not in _REGISTRY:
        raise KeyError(f"Domain '{name}' is not registered. Available domains: {list_domains()}")
    return _REGISTRY[name]


def list_domains() -> List[str]:
    """List all registered domain names."""
    return list(_REGISTRY.keys())


def clear_registry() -> None:
    """Clear all registered domains (primarily for testing)."""
    _REGISTRY.clear()
