# ProtoForge harness -- v1 (I2C)

Built and validated following the ProtoForge Build Guide, stages 0-12.

## Verified in this build (no Ollama required for these)
- devices/device_sensor.v, devices/device_controller.v: syntax-checked.
- tools/verilog_parser.py: correctly extracts ports from both devices.
- protocols/i2c/testbench_i2c.v: validated as a correct oracle against
  protocols/i2c/reference/good_master.v (all 4 checks pass, correct
  address/data captured) AND against reference/broken_master_no_stop.v
  (correctly flags ONLY the missing STOP condition, nothing else).
- tools/compile_tool.py, tools/simulation_tool.py, tools/judge.py: all
  exercised against both reference masters above.
- agent_core/agent.py: full generate->compile->repair->simulate->judge->
  repair->log loop verified end-to-end via test_agent_integration.py,
  which stubs the LLM call (no Ollama needed) to prove the orchestration
  and repair-loop logic itself is correct.

## What you need to add on your machine
1. `ollama pull qwen2.5-coder:7b` (or hermes4, deepseek-coder, etc --
   just change agent/ollama_client.py's DEFAULT_MODEL or pass model=...)
2. `pip install -r requirements.txt`
3. Run one real attempt: `python3 agent_core/agent.py`
4. Run a batch for a real pass-rate number: `python3 main.py 20`

## Sanity check anytime (no Ollama needed)
`python3 test_agent_integration.py` -- re-proves the harness logic itself
still works, independent of model quality.

## Next steps (from here, not yet built)
- Persistent few-shot memory: save passing top_glue.v files keyed by
  device-pair signature, retrieve as few-shot examples for future prompts.
- LoRA fine-tuning on accumulated results/runs.jsonl "pass" examples.
- UART/SPI: copy protocols/i2c/ structure once I2C's numbers look good.
