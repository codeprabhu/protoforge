"""
Integration test for agent_core.agent.run_once() that stubs out the LLM
call so we can prove the orchestration logic (generate -> compile -> repair
loop -> simulate -> judge -> repair loop -> log) works correctly without
needing a real Ollama server.

Stub behavior: first "generation" returns the intentionally-broken
(no-STOP) master. This should compile fine but fail judge() on the "stop"
check. The stubbed repair call then returns the known-good master, which
should pass. This exercises the simulation-failure repair path -- the
harder of the two repair paths to get right.
"""
import json
import os

import agent.ollama_client as ollama_client

call_count = {"n": 0}
BROKEN = open("protocols/i2c/reference/broken_master_no_stop.v").read()
GOOD = open("protocols/i2c/reference/good_master.v").read()


def fake_generate_verilog(prompt, model=None, temperature=None):
    call_count["n"] += 1
    if call_count["n"] == 1:
        print("[stub] first call -> returning BROKEN master (no STOP)")
        return f"```verilog\n{BROKEN}\n```"
    else:
        print(f"[stub] call #{call_count['n']} (repair) -> returning GOOD master")
        return f"```verilog\n{GOOD}\n```"


ollama_client.generate_verilog = fake_generate_verilog

from agent_core import agent  # import after monkeypatch so it picks up the stub

if os.path.exists(agent.RESULTS_LOG):
    os.remove(agent.RESULTS_LOG)

result = agent.run_once(model="stub-model")
print()
print("=== run_once() result ===")
print(json.dumps(result, indent=2))

assert result["final"] == "pass", "expected the repair loop to recover and pass"
assert len(result["attempts"]) == 2, "expected exactly 2 attempts: 1 failure + 1 pass"
assert result["attempts"][0]["failed_checks"] == ["stop"], "expected the first attempt to fail only on STOP"
assert call_count["n"] == 2, "expected exactly 2 LLM calls: 1 generate + 1 repair"

print()
print("ALL ASSERTIONS PASSED - repair loop works end to end.")
