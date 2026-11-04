"""Integration test for agent_core.agent.run_once() using a stubbed model."""

import agent.ollama_client as ollama_client
import agent_core.agent as agent


def test_agent_integration_repair_loop(run_config, monkeypatch):
    broken = open("protocols/i2c/reference/broken_master_no_stop_reset_safe.v").read()
    good = open("protocols/i2c/reference/good_master_reset_safe.v").read()

    call_count = {"n": 0}

    def fake_generate_verilog(prompt, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return f"```verilog\n{broken}\n```"
        return f"```verilog\n{good}\n```"

    monkeypatch.setattr(ollama_client, "generate_verilog", fake_generate_verilog)

    result = agent.run_once(model="stub-model", config=run_config)

    assert result["final"] == "pass", "expected the repair loop to recover and pass"
    assert len(result["attempts"]) == 2, "expected exactly 2 attempts: 1 failure + 1 pass"
    assert set(result["attempts"][0]["failed_checks"]) == {"stop", "single_stop"}, \
        "expected the first attempt to fail on the missing STOP checks"
    assert call_count["n"] == 2, "expected exactly 2 LLM calls: 1 generate + 1 repair"
