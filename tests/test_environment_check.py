"""Unit tests for environment and toolchain inspection."""

import tools.environment_check as env_check

REQUIRED_RTL_TOOLS = ["iverilog", "vvp"]
GATE_TOOLS = ["verilator", "yosys"]


def test_environment_check_reports_a_consistent_readiness_verdict():
    report = env_check.check_environment()

    assert isinstance(report["ready_for_rtl_sim"], bool)
    assert isinstance(report["ready_for_full_gate"], bool)
    assert "python" in report
    assert report["python"]["executable"]

    # The headline flags must be derivable from the detail lists; this is the
    # invariant a consumer of the report actually relies on.
    assert report["ready_for_rtl_sim"] == (report["missing_required_rtl_tools"] == [])
    assert report["ready_for_full_gate"] == (
        report["missing_required_rtl_tools"] == [] and report["missing_gate_tools"] == []
    )
    for name in report["missing_required_rtl_tools"] + report["missing_gate_tools"]:
        assert name in report["tools"]


def test_every_reported_tool_has_a_boolean_availability_flag():
    tools = env_check.check_environment()["tools"]

    for required in REQUIRED_RTL_TOOLS + GATE_TOOLS + ["ollama"]:
        assert required in tools, f"{required} missing from the environment report"
        info = tools[required]
        assert isinstance(info["available"], bool)
        assert info["role"]
        if info["available"]:
            assert info["path"], f"{required} reported available without a path"
            assert info["version"], f"{required} reported available without a version"


def test_ollama_entry_reports_service_reachability_separately_from_installation():
    ollama = env_check.check_environment()["tools"]["ollama"]
    # Installed CLI and running service are different failure modes and must
    # not be conflated (a pulled model with a stopped daemon is a common case).
    assert isinstance(ollama["installed"], bool)
    assert isinstance(ollama["service_reachable"], bool)
    assert isinstance(ollama["models"], list)


def test_get_command_version_nonexistent():
    assert env_check.get_command_version("non_existent_binary_foo_bar_xyz") is None


def test_get_command_version_reads_a_real_tool():
    version = env_check.get_command_version("python3", "--version")
    assert version is not None
    assert "Python" in version
