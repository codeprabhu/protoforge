"""Regression tests for domain discovery.

These guard a real defect found during the v1 cleanup: ``get_domain`` and
``list_domains`` contained ``try: pass`` / ``except ImportError: pass``
skeletons where the lazy adapter import should have been. They therefore only
worked when some other module had already imported ``domains.i2c.adapter``;
importing ``domains.registry`` on its own raised

    KeyError: "Domain 'i2c' is not registered. Available domains: []"
"""

import pytest

import domains.registry as registry


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Run each check against a freshly emptied registry."""
    saved = dict(registry._REGISTRY)
    registry._REGISTRY.clear()
    try:
        yield
    finally:
        registry._REGISTRY.clear()
        registry._REGISTRY.update(saved)


def test_get_domain_lazily_registers_i2c_without_prior_import():
    # Nothing has populated the registry in this isolated fixture.
    adapter = registry.get_domain("i2c")
    assert adapter.name == "i2c"


def test_list_domains_reports_available_builtins():
    assert registry.list_domains() == ["i2c"]


def test_unknown_domain_raises_with_available_names():
    registry.get_domain("i2c")
    with pytest.raises(KeyError) as excinfo:
        registry.get_domain("no_such_domain")
    assert "i2c" in str(excinfo.value)
    assert "no_such_domain" in str(excinfo.value)


def test_repeated_lookup_returns_the_same_adapter_instance():
    first = registry.get_domain("i2c")
    second = registry.get_domain("i2c")
    assert first is second
