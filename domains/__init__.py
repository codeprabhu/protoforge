"""Domain adapters for ProtoForge."""

from domains.base import BaseDomainAdapter
from domains.registry import register_domain, get_domain, list_domains

__all__ = ["BaseDomainAdapter", "register_domain", "get_domain", "list_domains"]
