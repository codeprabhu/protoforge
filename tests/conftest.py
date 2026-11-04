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
