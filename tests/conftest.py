"""Shared fixtures for the test suite."""

from __future__ import annotations

import pytest

from agent_core.config import Config


@pytest.fixture
def run_config(tmp_path):
    """A Config whose artifacts stay inside the test's tmp_path."""
    return Config(
        output_root=str(tmp_path / "runs"),
        results_log=str(tmp_path / "runs.jsonl"),
        trace_dir=str(tmp_path / "traces"),
    )


@pytest.fixture(autouse=True)
def _isolate_rtl_memory(tmp_path, monkeypatch):
    """Redirect the verified-example store into tmp_path.

    ``run_once`` records passing/failed candidates by default, and several
    tests call it without overriding ``on_success``/``on_failure``. Without
    this fixture every test run appended new files to the *tracked*
    ``results/rtl_memory/`` tree, dirtying the repository as a side effect of
    ``pytest``.
    """
    import tools.rtl_memory as rtl_memory

    monkeypatch.setattr(rtl_memory, "DEFAULT_MEMORY_DIR", tmp_path / "rtl_memory")
