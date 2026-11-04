# Environment snapshot (Phase 0)

Recorded for reproducibility of the v1 baseline. Re-run
`python tools/environment_check.py` to refresh this on another machine.

| Component | Version / value |
|---|---|
| OS | Linux (x86-64) |
| Python | 3.14.8 |
| Icarus Verilog (`iverilog`) | 13.0 (stable) |
| Icarus runtime (`vvp`) | 13.0 (stable) |
| Verilator | 5.052 2026-09-05 |
| Yosys | 0.66 |
| Ollama | 0.24.0 |
| Ollama model | `qwen2.5-coder:7b` |
| Model digest | `dae161e27b0e90dd1856c8bb3209201fd6736d8eb66298e75ed87571486f4364` |
| GPU | NVIDIA GeForce RTX 3050 Laptop (4 GiB); model may partially run on CPU |

## Python environment

The project uses a local virtualenv at `.venv` (git-ignored):

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

`requirements.txt` lists `pydantic` explicitly because the code imports it
directly; relying on it arriving transitively through `mcp` is fragile.

## Baseline invocation

```bash
.venv/bin/python -m pytest -q tests
.venv/bin/python -m agent_core.agent
PROTOFORGE_BATCH_TEMPERATURE=0.25 .venv/bin/python main.py 5
```
