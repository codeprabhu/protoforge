"""Phase 2 regression: runs are isolated on disk and config is explicit."""

from pathlib import Path

import pytest

from agent_core.config import Config, RunPaths
from agent_core import agent as pipeline

ROOT = Path(__file__).resolve().parents[1]


def _good_with_marker(marker: str) -> str:
    good = (ROOT / "protocols/i2c/reference/good_master_reset_safe.v").read_text()
    return good.replace("endmodule", f"    // candidate marker {marker}\nendmodule")


def test_two_runs_do_not_overwrite_each_others_candidate(tmp_path):
    seen = []

    def fake_generate(devices, spec_path, **kwargs):
        marker = f"seed-{kwargs.get('seed')}"
        seen.append(marker)
        return _good_with_marker(marker)

    cfg_a = Config(
        output_root=str(tmp_path / "run-a"),
        results_log=str(tmp_path / "a.jsonl"),
        seed=1,
    )
    cfg_b = Config(
        output_root=str(tmp_path / "run-b"),
        results_log=str(tmp_path / "b.jsonl"),
        seed=2,
    )
    common = dict(
        model="stub",
        generate_fn=fake_generate,
        hardware_check_fn=lambda *a, **k: {"success": True, "stage": "passed"},
        on_success=lambda *a, **k: tmp_path / "ok.v",
        on_failure=lambda *a, **k: tmp_path / "bad.v",
    )
    run_a = pipeline.run_once(config=cfg_a, **common)
    run_b = pipeline.run_once(config=cfg_b, **common)

    assert seen == ["seed-1", "seed-2"]
    assert run_a["run_dir"] != run_b["run_dir"]
    a_code = (Path(run_a["run_dir"]) / "attempt_0.v").read_text()
    b_code = (Path(run_b["run_dir"]) / "attempt_0.v").read_text()
    assert "candidate marker seed-1" in a_code
    assert "candidate marker seed-2" in b_code
    assert "seed-2" not in a_code
    assert run_a["final"] == "pass" and run_b["final"] == "pass"


def test_run_paths_are_unique_and_created(tmp_path):
    first = RunPaths.create(tmp_path)
    second = RunPaths.create(tmp_path)
    assert first.run_id != second.run_id
    assert first.run_dir.is_dir() and second.run_dir.is_dir()
    assert first.attempt_code(0).name == "attempt_0.v"
    assert first.attempt_vvp(2).name == "attempt_2.vvp"
    assert first.attempt_prompt(1).name == "attempt_1_prompt.txt"


def test_config_from_env_reads_overrides(monkeypatch):
    monkeypatch.setenv("PROTOFORGE_MODEL", "env-model")
    monkeypatch.setenv("PROTOFORGE_MAX_REPAIRS", "4")
    monkeypatch.setenv("PROTOFORGE_SEED", "7")
    monkeypatch.setenv("PROTOFORGE_TEMPERATURE", "0.25")
    monkeypatch.setenv("PROTOFORGE_DEVICE_PATHS", "devices/device_sensor.v")
    monkeypatch.setenv("PROTOFORGE_NO_RETRIEVAL", "1")

    cfg = Config.from_env()
    assert cfg.model == "env-model"
    assert cfg.max_repairs == 4
    assert cfg.seed == 7
    assert cfg.temperature == 0.25
    assert cfg.device_paths == ("devices/device_sensor.v",)
    assert cfg.retrieval is False

    overridden = Config.from_env(model="explicit")
    assert overridden.model == "explicit"


def test_config_rejects_empty_devices():
    with pytest.raises(ValueError):
        Config(device_paths=())
    with pytest.raises(ValueError):
        Config(max_repairs=-1)
