import sys

from agent_core.agent import run_once

N_RUNS = int(sys.argv[1]) if len(sys.argv) > 1 else 20
MODEL = sys.argv[2] if len(sys.argv) > 2 else None  # None -> agent/ollama_client.DEFAULT_MODEL

if __name__ == "__main__":
    results = [run_once(model=MODEL) for _ in range(N_RUNS)]

    passed = sum(1 for r in results if r["final"] == "pass")
    compile_failed = sum(1 for r in results if r["final"] == "compile_failed")
    sim_failed = sum(1 for r in results if r["final"] == "sim_checks_failed")

    print(f"Model: {MODEL or '(default)'}")
    print(f"Pass rate:            {passed}/{N_RUNS}")
    print(f"Compile-failed:       {compile_failed}/{N_RUNS}")
    print(f"Sim-checks-failed:    {sim_failed}/{N_RUNS}")
    print(f"Full log: results/runs.jsonl")
