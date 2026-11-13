from __future__ import annotations

import hashlib
import json
import threading
import time

from agent_core.config import Config
from core.experiments import ExperimentManager, ExperimentStore
from ui.model_registry import ModelRegistry


def build_registry(path, models=("alpha", "beta")):
    registry = ModelRegistry(path)
    registry.register_provider({"provider_id": "local", "provider_type": "ollama", "display_name": "Local Ollama"})
    entries = []
    for name in models:
        entries.append(registry.register_model({
            "provider_id": "local", "model_identifier": name, "display_name": name, "local": True,
        }))
    return registry, entries


def fake_runner_factory(*, fail_model=None, barrier=None, seen=None):
    active = 0
    active_lock = threading.Lock()
    maximum = 0

    def runner(*, config: Config, run_id, progress_fn, on_success, on_failure):
        nonlocal active, maximum
        with active_lock:
            active += 1
            maximum = max(maximum, active)
        if seen is not None:
            seen.append((config.model, config.seed, config.output_root, config.retrieval, config.repair_model, config.analyst_model))
        progress_fn("stage_finished", {"stage": "compile"})
        if barrier is not None:
            barrier.wait(timeout=3)
        time.sleep(0.025)
        run_dir = __import__("pathlib").Path(config.output_root) / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        code = f"module top; // {config.model}\nendmodule\n"
        (run_dir / "attempt_0.v").write_text(code, encoding="utf-8")
        passed = config.model != fail_model
        (run_dir / "attempt_0.json").write_text(json.dumps({
            "attempt": 0, "outcome": "pass" if passed else "compile_failed",
            "repair": False, "final_code": code,
            "code_sha256": hashlib.sha256(code.encode()).hexdigest(),
            "model_metadata": {"prompt_eval_count": 5, "eval_count": 7, "total_duration": 1_500_000_000},
            "stages": [{"stage": "structural", "passed": True}, {"stage": "compile", "passed": passed},
                       {"stage": "hardware", "passed": passed}, {"stage": "simulation", "passed": passed},
                       {"stage": "judge", "passed": passed}],
        }), encoding="utf-8")
        with active_lock:
            active -= 1
        return {"run_id": run_id, "run_dir": str(run_dir), "final": "pass" if passed else "compile_failed"}

    runner.maximum_active = lambda: maximum
    return runner


def wait_terminal(manager, experiment_id, timeout=4):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        record = manager.get(experiment_id)
        if record["status"] in {"completed", "partially_completed", "failed", "cancelled"}:
            return record
        time.sleep(0.02)
    raise AssertionError("experiment did not reach a terminal status")


def test_experiment_metadata_and_candidate_workspaces_are_persisted(tmp_path):
    registry, models = build_registry(tmp_path / "registry.json")
    seen = []
    manager = ExperimentManager(registry=registry, store=ExperimentStore(tmp_path / "experiments"), runner=fake_runner_factory(seen=seen))
    experiment = manager.create({"name": "controlled run", "model_ids": [item["model_id"] for item in models], "seed": 31})
    assert len(experiment["specification_sha256"]) == 64
    assert len(experiment["retrieval"]["corpus_sha256"]) == 64
    assert experiment["status"] == "created"
    manager.start(experiment["experiment_id"])
    final = wait_terminal(manager, experiment["experiment_id"])
    assert final["status"] == "completed"
    assert len({candidate["workspace"] for candidate in final["candidates"]}) == 2
    assert all(candidate["functional_success"] for candidate in final["candidates"])
    assert all(candidate["verification"]["judge"] is True for candidate in final["candidates"])
    assert all(len(candidate["prompt_sha256"]) == 64 for candidate in final["candidates"])
    assert all(len(candidate["retrieved_context_sha256"]) == 64 for candidate in final["candidates"])
    assert len({row[2] for row in seen}) == 2
    assert ExperimentStore(tmp_path / "experiments").get(experiment["experiment_id"])["status"] == "completed"


def test_concurrent_execution_respects_global_and_ollama_limits(tmp_path):
    registry, models = build_registry(tmp_path / "registry.json")
    runner = fake_runner_factory()
    manager = ExperimentManager(registry=registry, store=ExperimentStore(tmp_path / "experiments"), runner=runner)
    experiment = manager.create({
        "name": "parallel", "model_ids": [item["model_id"] for item in models],
        "execution_mode": "concurrent", "global_concurrency": 2, "local_concurrency": 1,
    })
    manager.start(experiment["experiment_id"])
    result = wait_terminal(manager, experiment["experiment_id"])
    assert result["status"] == "completed"
    assert runner.maximum_active() == 1


def test_partial_candidate_failure_is_isolated_and_retained(tmp_path):
    registry, models = build_registry(tmp_path / "registry.json")
    manager = ExperimentManager(
        registry=registry,
        store=ExperimentStore(tmp_path / "experiments"),
        runner=fake_runner_factory(fail_model="beta"),
    )
    experiment = manager.create({"name": "partial", "model_ids": [item["model_id"] for item in models]})
    manager.start(experiment["experiment_id"])
    result = wait_terminal(manager, experiment["experiment_id"])
    assert result["status"] == "partially_completed"
    assert {candidate["status"] for candidate in result["candidates"]} == {"completed", "failed"}
    assert all((tmp_path / "experiments" / result["experiment_id"] / candidate["workspace"]).is_dir() for candidate in result["candidates"])


def test_experiment_validation_rejects_unknown_model_and_limits(tmp_path):
    registry, models = build_registry(tmp_path / "registry.json")
    manager = ExperimentManager(registry=registry, store=ExperimentStore(tmp_path / "experiments"), runner=fake_runner_factory())
    for payload in (
        {"name": "bad model", "model_ids": ["missing"]},
        {"name": "bad limit", "model_ids": [models[0]["model_id"]], "global_concurrency": 99},
        {"name": "bad domain", "model_ids": [models[0]["model_id"]], "domain": "spi"},
    ):
        try:
            manager.create(payload)
        except ValueError:
            continue
        raise AssertionError("invalid experiment configuration accepted")


def test_cross_model_repair_selection_reaches_existing_repair_pipeline(tmp_path):
    registry, models = build_registry(tmp_path / "registry.json")
    seen = []
    manager = ExperimentManager(registry=registry, store=ExperimentStore(tmp_path / "experiments"), runner=fake_runner_factory(seen=seen))
    experiment = manager.create({
        "name": "cross repair", "model_ids": [models[0]["model_id"]],
        "repair_model_id": models[1]["model_id"],
    })
    assert experiment["repair"]["mode"] == "cross_model"
    manager.start(experiment["experiment_id"])
    assert wait_terminal(manager, experiment["experiment_id"])["status"] == "completed"
    assert seen[0][0] == "alpha"
    assert seen[0][4] == "beta"


def test_generator_analyst_and_repair_roles_are_configurable(tmp_path):
    registry, models = build_registry(tmp_path / "registry.json")
    seen = []
    manager = ExperimentManager(registry=registry, store=ExperimentStore(tmp_path / "experiments"), runner=fake_runner_factory(seen=seen))
    experiment = manager.create({
        "name": "three roles", "model_ids": [models[0]["model_id"]],
        "repair_model_id": models[1]["model_id"], "analyst_model_id": models[0]["model_id"],
    })
    manager.start(experiment["experiment_id"])
    assert wait_terminal(manager, experiment["experiment_id"])["status"] == "completed"
    assert seen[0][0] == "alpha"
    assert seen[0][4] == "beta"
    assert seen[0][5] == "alpha"


def test_experiment_cancellation_keeps_already_finished_candidate(tmp_path):
    registry, models = build_registry(tmp_path / "registry.json")
    gate = threading.Event()
    entered = threading.Event()

    def runner(*, config, run_id, progress_fn, on_success, on_failure):
        entered.set()
        gate.wait(timeout=3)
        path = __import__("pathlib").Path(config.output_root) / run_id
        path.mkdir(parents=True, exist_ok=True)
        code = "module top; endmodule\n"
        (path / "attempt_0.v").write_text(code)
        (path / "attempt_0.json").write_text(json.dumps({"attempt": 0, "outcome": "pass", "code_sha256": hashlib.sha256(code.encode()).hexdigest(), "final_code": code, "stages": [], "model_metadata": {}}))
        return {"run_id": run_id, "run_dir": str(path), "final": "pass"}

    manager = ExperimentManager(registry=registry, store=ExperimentStore(tmp_path / "experiments"), runner=runner)
    experiment = manager.create({"name": "cancel", "model_ids": [models[0]["model_id"]]})
    manager.start(experiment["experiment_id"])
    assert entered.wait(timeout=2)
    manager.cancel(experiment["experiment_id"])
    gate.set()
    deadline = time.monotonic() + 4
    result = manager.get(experiment["experiment_id"])
    while time.monotonic() < deadline and not result["candidates"][0]["finished_at"]:
        time.sleep(0.02)
        result = manager.get(experiment["experiment_id"])
    assert result["status"] == "cancelled"
    assert result["candidates"][0]["status"] == "cancelled"
    assert (tmp_path / "experiments" / result["experiment_id"] / result["candidates"][0]["workspace"]).exists()
