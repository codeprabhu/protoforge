"""
Proves the structural-check gate added to agent_core/agent.py actually
fires and triggers repair -- this is the gate that would have caught the
"model never instantiated the devices" bug directly, in one attempt,
instead of burning a full compile+simulate cycle to discover it.
"""
import json
import os

import agent.ollama_client as ollama_client

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

GOOD = open("protocols/i2c/reference/good_master.v").read()

call_count = {"n": 0}


def fake_generate_verilog(prompt, model=None, temperature=None):
    call_count["n"] += 1
    if call_count["n"] == 1:
        print("[stub] first call -> returning a standalone FSM with NO device instantiation")
        return f"```verilog\n{STANDALONE_FSM_NO_INSTANTIATION}\n```"
    else:
        print(f"[stub] call #{call_count['n']} (repair) -> returning GOOD master")
        return f"```verilog\n{GOOD}\n```"


ollama_client.generate_verilog = fake_generate_verilog

from agent_core import agent  # noqa: E402  (import after monkeypatch)

if os.path.exists(agent.RESULTS_LOG):
    os.remove(agent.RESULTS_LOG)

result = agent.run_once(model="stub-model")
print()
print(json.dumps(result, indent=2))

assert result["attempts"][0]["stage"] == "structural", "expected the FIRST attempt to be caught by the structural gate, before ever compiling"
assert "instantiates_device_sensor" in result["attempts"][0]["failed_checks"]
assert "instantiates_device_controller" in result["attempts"][0]["failed_checks"]
assert result["final"] == "pass"

print()
print("ALL ASSERTIONS PASSED - structural gate catches missing instantiation before compile, and repair recovers.")
