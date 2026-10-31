"""Unit tests for environment and toolchain inspection."""

import tools.environment_check as env_check


def test_environment_check_structure():
    report = env_check.check_environment()
    assert "ready_for_rtl_sim" in report
    assert "missing_required_rtl_tools" in report
    assert "tools" in report
    assert "python" in report

    tools = report["tools"]
    for required in ["iverilog", "vvp", "verilator", "yosys", "clang", "gcc", "make", "ollama"]:
        assert required in tools
        assert "available" in tools[required] or "installed" in tools[required]


def test_get_command_version_nonexistent():
    res = env_check.get_command_version("non_existent_binary_foo_bar_xyz")
    assert res is None
