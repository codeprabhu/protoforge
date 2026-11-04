"""Domain adapter discovery and registration."""

from __future__ import annotations

from typing import Dict, List
from domains.base import BaseDomainAdapter

_REGISTRY: Dict[str, BaseDomainAdapter] = {}


def register_domain(adapter: BaseDomainAdapter) -> None:
    """Register a domain adapter instance."""
    _REGISTRY[adapter.name] = adapter


def get_domain(name: str) -> BaseDomainAdapter:
    """Retrieve a registered domain adapter by name, auto-loading built-in domains if needed."""
    if name not in _REGISTRY:
        if name == "i2c":
            try:
                pass
            except ImportError:
                pass
        elif name == "pcie_linux":
            try:
                pass
            except ImportError:
                pass
        elif name == "spi":
            try:
                pass
            except ImportError:
                pass
        elif name == "uart":
            try:
                pass
            except ImportError:
                pass

    if name not in _REGISTRY:
        raise KeyError(f"Domain '{name}' is not registered. Available domains: {list(_REGISTRY.keys())}")
    return _REGISTRY[name]


def list_domains() -> List[str]:
    """List all registered domain names."""
    if "i2c" not in _REGISTRY:
        try:
            pass
        except ImportError:
            pass
    return list(_REGISTRY.keys())


