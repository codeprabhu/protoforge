"""Test that missing device instantiation is caught by structural checks."""

import pytest
import agent.ollama_client as ollama_client
import agent_core.agent as agent

STANDALONE_FSM_NO_INSTANTIATION = """
module top_glue (
    input  wire clk,
    input  wire rst_n,
    inout  wire sda,
    inout  wire scl,
    input  wire tb_start,
    output reg  tb_done
);
    reg [3:0] state;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) state <= 0;
        else state <= state + 1;
    end
endmodule
"""


def test_structural_gate_catches_missing_instantiations(run_config, monkeypatch):
    good = open("protocols/i2c/reference/good_master_reset_safe.v").read()
    call_count = {"n": 0}

    def fake_generate_verilog(prompt, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return f"```verilog\n{STANDALONE_FSM_NO_INSTANTIATION}\n```"
        else:
            return f"```verilog\n{good}\n```"

    monkeypatch.setattr(ollama_client, "generate_verilog", fake_generate_verilog)
    result = agent.run_once(model="stub-model", config=run_config)

    assert result["attempts"][0]["stage"] == "structural"
    assert "has_tb_done_logic" in result["attempts"][0]["failed_checks"]
    assert result["final"] == "pass"
